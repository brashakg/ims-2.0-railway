"""
IMS 2.0 - Inventory Management Engine
=====================================
Features:
1. Stock tracking per store with location codes (C1, D2, etc.)
2. Stock transfers between stores (barcodes removed/reprinted)
3. Stock acceptance workflow with mismatch escalation
4. Salesperson-assigned stock accountability
5. Daily stock count
6. Low stock alerts
7. Expiry tracking for contact lenses
8. Stock reservation for pending orders
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Tuple
import uuid


class StockMovementType(Enum):
    PURCHASE_IN = "PURCHASE_IN"           # GRN from vendor
    SALE_OUT = "SALE_OUT"                 # Sold to customer
    TRANSFER_OUT = "TRANSFER_OUT"         # Sent to another store
    TRANSFER_IN = "TRANSFER_IN"           # Received from another store
    RETURN_IN = "RETURN_IN"               # Customer return
    RETURN_OUT = "RETURN_OUT"             # Return to vendor
    ADJUSTMENT_PLUS = "ADJUSTMENT_PLUS"   # Stock count adjustment (+)
    ADJUSTMENT_MINUS = "ADJUSTMENT_MINUS" # Stock count adjustment (-)
    DAMAGED = "DAMAGED"                   # Damaged/written off
    RESERVED = "RESERVED"                 # Reserved for order
    UNRESERVED = "UNRESERVED"             # Released from reservation


class TransferStatus(Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    CANCELLED = "CANCELLED"


class StockAcceptanceStatus(Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


@dataclass
class Product:
    id: str
    sku: str
    name: str
    barcode: Optional[str]
    category_code: str
    brand_code: Optional[str]
    mrp: Decimal
    offer_price: Decimal
    
    # For contact lenses
    track_expiry: bool = False
    track_batch: bool = False


@dataclass
class StockUnit:
    """Individual stock record at a store"""
    id: str
    product_id: str
    store_id: str
    
    # Quantity
    quantity: int = 0
    reserved_quantity: int = 0  # Reserved for pending orders
    
    # Location within store
    location_code: Optional[str] = None  # C1, D2, S3 (Counter, Display, Shelf)
    location_description: Optional[str] = None
    
    # For batch/expiry tracking
    batch_code: Optional[str] = None
    expiry_date: Optional[date] = None
    
    # Acceptance status
    acceptance_status: StockAcceptanceStatus = StockAcceptanceStatus.PENDING
    accepted_by: Optional[str] = None
    accepted_at: Optional[datetime] = None
    
    # Salesperson assignment
    assigned_to_user_id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    
    # Barcode (store-specific, includes location)
    store_barcode: Optional[str] = None
    barcode_printed: bool = False
    barcode_printed_at: Optional[datetime] = None
    
    # Stock count
    last_count_quantity: Optional[int] = None
    last_count_at: Optional[datetime] = None
    last_count_by: Optional[str] = None
    
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    @property
    def available_quantity(self) -> int:
        """Quantity available for sale (excluding reserved)"""
        return max(0, self.quantity - self.reserved_quantity)
    
    @property
    def is_expired(self) -> bool:
        if not self.expiry_date:
            return False
        return self.expiry_date < date.today()
    
    @property
    def days_to_expiry(self) -> Optional[int]:
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days


@dataclass
class StockMovement:
    """Record of every stock movement for audit trail"""
    id: str
    stock_unit_id: str
    product_id: str
    store_id: str
    
    movement_type: StockMovementType
    quantity: int  # Positive for in, negative for out
    
    # Reference
    reference_type: Optional[str] = None  # ORDER, TRANSFER, GRN, ADJUSTMENT
    reference_id: Optional[str] = None
    
    # Before/After for audit
    quantity_before: int = 0
    quantity_after: int = 0
    
    # User
    created_by: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    
    notes: Optional[str] = None


@dataclass
class StockTransferItem:
    id: str
    product_id: str
    product_name: str
    product_sku: str
    
    quantity_sent: int = 0
    quantity_received: int = 0
    
    # Barcode handling
    barcode_removed: bool = False
    barcode_removed_by: Optional[str] = None
    new_barcode_printed: bool = False
    new_barcode: Optional[str] = None
    
    # Mismatch
    has_mismatch: bool = False
    mismatch_quantity: int = 0
    mismatch_notes: Optional[str] = None
    mismatch_resolved: bool = False
    mismatch_resolved_by: Optional[str] = None
    mismatch_resolution: Optional[str] = None


@dataclass
class StockTransfer:
    """Stock transfer between stores"""
    id: str
    transfer_number: str
    
    from_store_id: str
    from_store_name: str
    to_store_id: str
    to_store_name: str
    
    # Items
    items: List[StockTransferItem] = field(default_factory=list)
    
    # Status
    status: TransferStatus = TransferStatus.DRAFT
    
    # Sent
    sent_at: Optional[datetime] = None
    sent_by: Optional[str] = None
    sent_by_name: Optional[str] = None
    
    # Received
    received_at: Optional[datetime] = None
    received_by: Optional[str] = None
    received_by_name: Optional[str] = None
    
    # Approval (for Area Manager/Admin)
    requires_approval: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    
    # Notes
    notes: Optional[str] = None
    
    created_at: datetime = field(default_factory=datetime.now)
    created_by: str = ""
    
    @property
    def total_items(self) -> int:
        return len(self.items)
    
    @property
    def total_quantity_sent(self) -> int:
        return sum(item.quantity_sent for item in self.items)
    
    @property
    def total_quantity_received(self) -> int:
        return sum(item.quantity_received for item in self.items)
    
    @property
    def has_mismatches(self) -> bool:
        return any(item.has_mismatch for item in self.items)


@dataclass
class StockCount:
    """Daily stock count record"""
    id: str
    store_id: str
    count_date: date
    
    # Staff
    counted_by: str
    counted_by_name: str
    
    # Status
    status: str = "IN_PROGRESS"  # IN_PROGRESS, COMPLETED, VERIFIED
    
    # Summary
    total_products_counted: int = 0
    total_variances: int = 0
    total_variance_value: Decimal = Decimal("0")
    
    # Items
    items: List[dict] = field(default_factory=list)
    
    # Timing
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None


@dataclass
class LowStockAlert:
    product_id: str
    product_name: str
    store_id: str
    store_name: str
    current_quantity: int
    minimum_quantity: int
    reorder_quantity: int
    category_code: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass 
class ExpiryAlert:
    product_id: str
    product_name: str
    store_id: str
    store_name: str
    batch_code: str
    quantity: int
    expiry_date: date
    days_to_expiry: int
    created_at: datetime = field(default_factory=datetime.now)


class InventoryEngine:
    """
    Central inventory management engine.
    Handles all stock operations with full audit trail.
    """
    
    def __init__(self):
        # In-memory storage (replace with database in production)
        self.products: Dict[str, Product] = {}
        self.stock_units: Dict[str, StockUnit] = {}
        self.stock_movements: List[StockMovement] = []
        self.transfers: Dict[str, StockTransfer] = {}
        self.stock_counts: Dict[str, StockCount] = {}
        
        # Minimum stock levels per category
        self.min_stock_levels = {
            "FRAME": 2,
            "SUNGLASS": 2,
            "READING_GLASSES": 3,
            "OPTICAL_LENS": 5,
            "CONTACT_LENS": 5,
            "COLORED_CONTACT_LENS": 3,
            "ACCESSORY": 3,
            "WATCH": 1,
            "SMARTWATCH": 2,
            "SMARTGLASSES": 1,
            "WALL_CLOCK": 2,
        }
        
        # Expiry warning days
        self.expiry_warning_days = 90
    
    def generate_transfer_number(self, from_store_code: str, to_store_code: str) -> str:
        date_part = datetime.now().strftime("%Y%m%d")
        random_part = uuid.uuid4().hex[:4].upper()
        return f"TRF-{from_store_code}-{to_store_code}-{date_part}-{random_part}"
    
    def generate_store_barcode(self, product_sku: str, store_code: str, location_code: str) -> str:
        """Generate store-specific barcode including location"""
        random_part = uuid.uuid4().hex[:4].upper()
        return f"{store_code}-{location_code}-{product_sku}-{random_part}"
    
    # =========================================================================
    # STOCK OPERATIONS
    # =========================================================================
    
    def add_stock(
        self,
        product_id: str,
        store_id: str,
        quantity: int,
        user_id: str,
        movement_type: StockMovementType = StockMovementType.PURCHASE_IN,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        location_code: Optional[str] = None,
        batch_code: Optional[str] = None,
        expiry_date: Optional[date] = None,
        notes: Optional[str] = None
    ) -> Tuple[bool, str, Optional[StockUnit]]:
        """Add stock to a store"""
        
        if quantity <= 0:
            return False, "Quantity must be positive", None
        
        # Find or create stock unit
        stock_key = f"{product_id}:{store_id}:{batch_code or 'NO_BATCH'}"
        
        if stock_key in self.stock_units:
            stock_unit = self.stock_units[stock_key]
            quantity_before = stock_unit.quantity
            stock_unit.quantity += quantity
            stock_unit.updated_at = datetime.now()
        else:
            stock_unit = StockUnit(
                id=str(uuid.uuid4()),
                product_id=product_id,
                store_id=store_id,
                quantity=quantity,
                location_code=location_code,
                batch_code=batch_code,
                expiry_date=expiry_date,
                acceptance_status=StockAcceptanceStatus.PENDING
            )
            quantity_before = 0
            self.stock_units[stock_key] = stock_unit
        
        # Record movement
        movement = StockMovement(
            id=str(uuid.uuid4()),
            stock_unit_id=stock_unit.id,
            product_id=product_id,
            store_id=store_id,
            movement_type=movement_type,
            quantity=quantity,
            quantity_before=quantity_before,
            quantity_after=stock_unit.quantity,
            reference_type=reference_type,
            reference_id=reference_id,
            created_by=user_id,
            notes=notes
        )
        self.stock_movements.append(movement)
        
        return True, f"Added {quantity} units. New total: {stock_unit.quantity}", stock_unit
    
    def reduce_stock(
        self,
        product_id: str,
        store_id: str,
        quantity: int,
        user_id: str,
        movement_type: StockMovementType = StockMovementType.SALE_OUT,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        batch_code: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Reduce stock from a store"""
        
        if quantity <= 0:
            return False, "Quantity must be positive"
        
        stock_key = f"{product_id}:{store_id}:{batch_code or 'NO_BATCH'}"
        
        if stock_key not in self.stock_units:
            return False, "Stock not found"
        
        stock_unit = self.stock_units[stock_key]
        
        if stock_unit.available_quantity < quantity:
            return False, f"Insufficient stock. Available: {stock_unit.available_quantity}"
        
        quantity_before = stock_unit.quantity
        stock_unit.quantity -= quantity
        stock_unit.updated_at = datetime.now()
        
        # Record movement
        movement = StockMovement(
            id=str(uuid.uuid4()),
            stock_unit_id=stock_unit.id,
            product_id=product_id,
            store_id=store_id,
            movement_type=movement_type,
            quantity=-quantity,
            quantity_before=quantity_before,
            quantity_after=stock_unit.quantity,
            reference_type=reference_type,
            reference_id=reference_id,
            created_by=user_id,
            notes=notes
        )
        self.stock_movements.append(movement)
        
        return True, f"Reduced {quantity} units. New total: {stock_unit.quantity}"
    
    def reserve_stock(
        self,
        product_id: str,
        store_id: str,
        quantity: int,
        order_id: str,
        user_id: str
    ) -> Tuple[bool, str]:
        """Reserve stock for a pending order"""
        
        # Find stock unit with available quantity
        for key, stock_unit in self.stock_units.items():
            if (stock_unit.product_id == product_id and 
                stock_unit.store_id == store_id and
                stock_unit.available_quantity >= quantity):
                
                stock_unit.reserved_quantity += quantity
                stock_unit.updated_at = datetime.now()
                
                # Record movement
                movement = StockMovement(
                    id=str(uuid.uuid4()),
                    stock_unit_id=stock_unit.id,
                    product_id=product_id,
                    store_id=store_id,
                    movement_type=StockMovementType.RESERVED,
                    quantity=quantity,
                    quantity_before=stock_unit.reserved_quantity - quantity,
                    quantity_after=stock_unit.reserved_quantity,
                    reference_type="ORDER",
                    reference_id=order_id,
                    created_by=user_id
                )
                self.stock_movements.append(movement)
                
                return True, f"Reserved {quantity} units for order {order_id}"
        
        return False, "Insufficient stock available for reservation"
    
    def release_reservation(
        self,
        product_id: str,
        store_id: str,
        quantity: int,
        order_id: str,
        user_id: str
    ) -> Tuple[bool, str]:
        """Release reserved stock (order cancelled or fulfilled)"""
        
        for key, stock_unit in self.stock_units.items():
            if (stock_unit.product_id == product_id and 
                stock_unit.store_id == store_id and
                stock_unit.reserved_quantity >= quantity):
                
                stock_unit.reserved_quantity -= quantity
                stock_unit.updated_at = datetime.now()
                
                movement = StockMovement(
                    id=str(uuid.uuid4()),
                    stock_unit_id=stock_unit.id,
                    product_id=product_id,
                    store_id=store_id,
                    movement_type=StockMovementType.UNRESERVED,
                    quantity=-quantity,
                    quantity_before=stock_unit.reserved_quantity + quantity,
                    quantity_after=stock_unit.reserved_quantity,
                    reference_type="ORDER",
                    reference_id=order_id,
                    created_by=user_id
                )
                self.stock_movements.append(movement)
                
                return True, f"Released {quantity} units from reservation"
        
        return False, "No matching reservation found"
    
    # =========================================================================
    # STOCK ACCEPTANCE (From HQ/Transfers)
    # =========================================================================
    
    def accept_stock(
        self,
        stock_unit_id: str,
        user_id: str,
        location_code: str,
        store_code: str
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Store manager accepts stock and assigns location.
        Generates new store-specific barcode.
        """
        
        stock_unit = None
        for su in self.stock_units.values():
            if su.id == stock_unit_id:
                stock_unit = su
                break
        
        if not stock_unit:
            return False, "Stock unit not found", None
        
        if stock_unit.acceptance_status == StockAcceptanceStatus.ACCEPTED:
            return False, "Stock already accepted", None
        
        # Generate store barcode
        product = self.products.get(stock_unit.product_id)
        product_sku = product.sku if product else "UNKNOWN"
        
        new_barcode = self.generate_store_barcode(product_sku, store_code, location_code)
        
        stock_unit.acceptance_status = StockAcceptanceStatus.ACCEPTED
        stock_unit.accepted_by = user_id
        stock_unit.accepted_at = datetime.now()
        stock_unit.location_code = location_code
        stock_unit.store_barcode = new_barcode
        stock_unit.barcode_printed = False  # Needs to be printed
        stock_unit.updated_at = datetime.now()
        
        return True, f"Stock accepted. New barcode: {new_barcode}", new_barcode
    
    def escalate_stock_mismatch(
        self,
        stock_unit_id: str,
        user_id: str,
        expected_quantity: int,
        actual_quantity: int,
        notes: str
    ) -> Tuple[bool, str, str]:
        """Escalate stock mismatch to HQ"""
        
        stock_unit = None
        for su in self.stock_units.values():
            if su.id == stock_unit_id:
                stock_unit = su
                break
        
        if not stock_unit:
            return False, "Stock unit not found", ""
        
        stock_unit.acceptance_status = StockAcceptanceStatus.ESCALATED
        stock_unit.updated_at = datetime.now()
        
        # Create escalation task (in real system, this would create a Task)
        escalation_id = f"ESC-{uuid.uuid4().hex[:8].upper()}"
        
        return True, f"Mismatch escalated to HQ. Escalation ID: {escalation_id}", escalation_id
    
    def mark_barcode_printed(self, stock_unit_id: str) -> Tuple[bool, str]:
        """Mark that barcode has been printed at store"""
        
        for su in self.stock_units.values():
            if su.id == stock_unit_id:
                su.barcode_printed = True
                su.barcode_printed_at = datetime.now()
                su.updated_at = datetime.now()
                return True, "Barcode marked as printed"
        
        return False, "Stock unit not found"
    
    # =========================================================================
    # STOCK TRANSFERS
    # =========================================================================
    
    def create_transfer(
        self,
        from_store_id: str,
        from_store_name: str,
        from_store_code: str,
        to_store_id: str,
        to_store_name: str,
        to_store_code: str,
        user_id: str,
        requires_approval: bool = False
    ) -> StockTransfer:
        """Create a new stock transfer"""
        
        transfer = StockTransfer(
            id=str(uuid.uuid4()),
            transfer_number=self.generate_transfer_number(from_store_code, to_store_code),
            from_store_id=from_store_id,
            from_store_name=from_store_name,
            to_store_id=to_store_id,
            to_store_name=to_store_name,
            requires_approval=requires_approval,
            created_by=user_id
        )
        
        self.transfers[transfer.id] = transfer
        return transfer
    
    def add_transfer_item(
        self,
        transfer_id: str,
        product_id: str,
        product_name: str,
        product_sku: str,
        quantity: int
    ) -> Tuple[bool, str]:
        """Add item to transfer"""
        
        transfer = self.transfers.get(transfer_id)
        if not transfer:
            return False, "Transfer not found"
        
        if transfer.status != TransferStatus.DRAFT:
            return False, "Cannot modify transfer after it's sent"
        
        # Check stock availability
        total_available = 0
        for su in self.stock_units.values():
            if su.product_id == product_id and su.store_id == transfer.from_store_id:
                total_available += su.available_quantity
        
        if total_available < quantity:
            return False, f"Insufficient stock. Available: {total_available}"
        
        item = StockTransferItem(
            id=str(uuid.uuid4()),
            product_id=product_id,
            product_name=product_name,
            product_sku=product_sku,
            quantity_sent=quantity
        )
        
        transfer.items.append(item)
        return True, f"Added {quantity} x {product_name} to transfer"
    
    def send_transfer(
        self,
        transfer_id: str,
        user_id: str,
        user_name: str
    ) -> Tuple[bool, str]:
        """
        Send transfer - reduces stock from source store.
        IMPORTANT: Barcodes must be removed before sending!
        """
        
        transfer = self.transfers.get(transfer_id)
        if not transfer:
            return False, "Transfer not found"
        
        if transfer.status != TransferStatus.DRAFT:
            return False, f"Transfer already in status: {transfer.status.value}"
        
        if not transfer.items:
            return False, "Cannot send empty transfer"
        
        # Check all barcodes are removed
        for item in transfer.items:
            if not item.barcode_removed:
                return False, f"Remove barcode from {item.product_name} before sending"
        
        # Reduce stock from source store
        for item in transfer.items:
            success, msg = self.reduce_stock(
                product_id=item.product_id,
                store_id=transfer.from_store_id,
                quantity=item.quantity_sent,
                user_id=user_id,
                movement_type=StockMovementType.TRANSFER_OUT,
                reference_type="TRANSFER",
                reference_id=transfer.id
            )
            if not success:
                return False, f"Failed to reduce stock for {item.product_name}: {msg}"
        
        transfer.status = TransferStatus.SENT
        transfer.sent_at = datetime.now()
        transfer.sent_by = user_id
        transfer.sent_by_name = user_name
        
        return True, f"Transfer {transfer.transfer_number} sent with {transfer.total_quantity_sent} items"
    
    def mark_barcode_removed(
        self,
        transfer_id: str,
        item_id: str,
        user_id: str
    ) -> Tuple[bool, str]:
        """Mark that barcode has been removed from item before transfer"""
        
        transfer = self.transfers.get(transfer_id)
        if not transfer:
            return False, "Transfer not found"
        
        for item in transfer.items:
            if item.id == item_id:
                item.barcode_removed = True
                item.barcode_removed_by = user_id
                return True, "Barcode removal confirmed"
        
        return False, "Item not found in transfer"
    
    def receive_transfer(
        self,
        transfer_id: str,
        user_id: str,
        user_name: str,
        received_quantities: Dict[str, int]  # item_id -> quantity received
    ) -> Tuple[bool, str]:
        """
        Receive transfer at destination store.
        Checks for mismatches and adds stock.
        """
        
        transfer = self.transfers.get(transfer_id)
        if not transfer:
            return False, "Transfer not found"
        
        if transfer.status not in [TransferStatus.SENT, TransferStatus.IN_TRANSIT]:
            return False, f"Cannot receive transfer in status: {transfer.status.value}"
        
        has_mismatch = False
        
        for item in transfer.items:
            received_qty = received_quantities.get(item.id, 0)
            item.quantity_received = received_qty
            
            if received_qty != item.quantity_sent:
                item.has_mismatch = True
                item.mismatch_quantity = item.quantity_sent - received_qty
                has_mismatch = True
            
            # Add stock to destination (will need acceptance)
            if received_qty > 0:
                self.add_stock(
                    product_id=item.product_id,
                    store_id=transfer.to_store_id,
                    quantity=received_qty,
                    user_id=user_id,
                    movement_type=StockMovementType.TRANSFER_IN,
                    reference_type="TRANSFER",
                    reference_id=transfer.id
                )
        
        transfer.received_at = datetime.now()
        transfer.received_by = user_id
        transfer.received_by_name = user_name
        
        if has_mismatch:
            transfer.status = TransferStatus.PARTIALLY_RECEIVED
            return True, f"Transfer received with mismatches. Please escalate."
        else:
            transfer.status = TransferStatus.RECEIVED
            return True, f"Transfer {transfer.transfer_number} received successfully"
    
    # =========================================================================
    # STOCK COUNT
    # =========================================================================
    
    def start_stock_count(
        self,
        store_id: str,
        user_id: str,
        user_name: str
    ) -> StockCount:
        """Start daily stock count"""
        
        count = StockCount(
            id=str(uuid.uuid4()),
            store_id=store_id,
            count_date=date.today(),
            counted_by=user_id,
            counted_by_name=user_name
        )
        
        self.stock_counts[count.id] = count
        return count
    
    def record_count_item(
        self,
        count_id: str,
        product_id: str,
        product_name: str,
        system_quantity: int,
        actual_quantity: int,
        user_id: str
    ) -> Tuple[bool, str]:
        """Record counted quantity for a product"""
        
        count = self.stock_counts.get(count_id)
        if not count:
            return False, "Stock count not found"
        
        if count.status == "COMPLETED":
            return False, "Stock count already completed"
        
        variance = actual_quantity - system_quantity
        
        count.items.append({
            "product_id": product_id,
            "product_name": product_name,
            "system_quantity": system_quantity,
            "actual_quantity": actual_quantity,
            "variance": variance,
            "counted_at": datetime.now().isoformat(),
            "counted_by": user_id
        })
        
        count.total_products_counted += 1
        if variance != 0:
            count.total_variances += 1
        
        return True, f"Recorded: {product_name} - System: {system_quantity}, Actual: {actual_quantity}, Variance: {variance}"
    
    def complete_stock_count(
        self,
        count_id: str,
        user_id: str,
        apply_adjustments: bool = False
    ) -> Tuple[bool, str]:
        """Complete stock count and optionally apply adjustments"""
        
        count = self.stock_counts.get(count_id)
        if not count:
            return False, "Stock count not found"
        
        count.status = "COMPLETED"
        count.completed_at = datetime.now()
        
        if apply_adjustments and count.total_variances > 0:
            # Apply adjustments for each variance
            for item in count.items:
                if item["variance"] != 0:
                    if item["variance"] > 0:
                        self.add_stock(
                            product_id=item["product_id"],
                            store_id=count.store_id,
                            quantity=item["variance"],
                            user_id=user_id,
                            movement_type=StockMovementType.ADJUSTMENT_PLUS,
                            reference_type="STOCK_COUNT",
                            reference_id=count.id,
                            notes=f"Stock count adjustment"
                        )
                    else:
                        self.reduce_stock(
                            product_id=item["product_id"],
                            store_id=count.store_id,
                            quantity=abs(item["variance"]),
                            user_id=user_id,
                            movement_type=StockMovementType.ADJUSTMENT_MINUS,
                            reference_type="STOCK_COUNT",
                            reference_id=count.id,
                            notes=f"Stock count adjustment"
                        )
            
            return True, f"Stock count completed. {count.total_variances} adjustments applied."
        
        return True, f"Stock count completed. {count.total_variances} variances recorded (not applied)."
    
    # =========================================================================
    # SALESPERSON STOCK ASSIGNMENT
    # =========================================================================
    
    def assign_stock_to_salesperson(
        self,
        stock_unit_id: str,
        user_id: str,
        assigned_by: str
    ) -> Tuple[bool, str]:
        """Assign stock accountability to a salesperson"""
        
        for su in self.stock_units.values():
            if su.id == stock_unit_id:
                su.assigned_to_user_id = user_id
                su.assigned_at = datetime.now()
                su.updated_at = datetime.now()
                return True, "Stock assigned to salesperson"
        
        return False, "Stock unit not found"
    
    def get_salesperson_stock(self, user_id: str, store_id: str) -> List[StockUnit]:
        """Get all stock assigned to a salesperson"""
        
        return [
            su for su in self.stock_units.values()
            if su.assigned_to_user_id == user_id and su.store_id == store_id
        ]
    
    # =========================================================================
    # ALERTS
    # =========================================================================
    
    def get_low_stock_alerts(self, store_id: str) -> List[LowStockAlert]:
        """Get low stock alerts for a store"""
        
        alerts = []
        
        # Aggregate stock by product
        product_stock: Dict[str, int] = {}
        for su in self.stock_units.values():
            if su.store_id == store_id:
                if su.product_id not in product_stock:
                    product_stock[su.product_id] = 0
                product_stock[su.product_id] += su.available_quantity
        
        for product_id, quantity in product_stock.items():
            product = self.products.get(product_id)
            if not product:
                continue
            
            min_level = self.min_stock_levels.get(product.category_code, 1)
            
            if quantity <= min_level:
                alerts.append(LowStockAlert(
                    product_id=product_id,
                    product_name=product.name,
                    store_id=store_id,
                    store_name="",  # Would be populated from store lookup
                    current_quantity=quantity,
                    minimum_quantity=min_level,
                    reorder_quantity=min_level * 2,
                    category_code=product.category_code
                ))
        
        return alerts
    
    def get_expiry_alerts(self, store_id: str) -> List[ExpiryAlert]:
        """Get expiry alerts for contact lenses and other dated products"""
        
        alerts = []
        warning_threshold = date.today() + timedelta(days=self.expiry_warning_days)
        
        for su in self.stock_units.values():
            if su.store_id == store_id and su.expiry_date:
                if su.expiry_date <= warning_threshold and su.quantity > 0:
                    product = self.products.get(su.product_id)
                    alerts.append(ExpiryAlert(
                        product_id=su.product_id,
                        product_name=product.name if product else "Unknown",
                        store_id=store_id,
                        store_name="",
                        batch_code=su.batch_code or "",
                        quantity=su.quantity,
                        expiry_date=su.expiry_date,
                        days_to_expiry=su.days_to_expiry or 0
                    ))
        
        return sorted(alerts, key=lambda x: x.days_to_expiry)
    
    # =========================================================================
    # QUERIES
    # =========================================================================
    
    def get_store_stock(self, store_id: str) -> List[dict]:
        """Get all stock for a store"""
        
        result = []
        for su in self.stock_units.values():
            if su.store_id == store_id:
                product = self.products.get(su.product_id)
                result.append({
                    "stock_unit_id": su.id,
                    "product_id": su.product_id,
                    "product_name": product.name if product else "Unknown",
                    "product_sku": product.sku if product else "",
                    "quantity": su.quantity,
                    "reserved": su.reserved_quantity,
                    "available": su.available_quantity,
                    "location": su.location_code,
                    "barcode": su.store_barcode,
                    "batch": su.batch_code,
                    "expiry": str(su.expiry_date) if su.expiry_date else None,
                    "assigned_to": su.assigned_to_user_id,
                    "acceptance_status": su.acceptance_status.value
                })
        
        return result
    
    def get_stock_movement_history(
        self,
        product_id: Optional[str] = None,
        store_id: Optional[str] = None,
        days: int = 30
    ) -> List[StockMovement]:
        """Get stock movement history"""
        
        cutoff = datetime.now() - timedelta(days=days)
        
        return [
            m for m in self.stock_movements
            if m.created_at >= cutoff
            and (product_id is None or m.product_id == product_id)
            and (store_id is None or m.store_id == store_id)
        ]


# =============================================================================
# DEMO
# =============================================================================

def demo_inventory():
    """Demonstrate inventory management"""
    
    print("=" * 70)
    print("IMS 2.0 INVENTORY MANAGEMENT - DEMO")
    print("=" * 70)
    
    engine = InventoryEngine()
    
    # Add products
    frame = Product(
        id="prod-001",
        sku="RB-5154-2000",
        name="Ray-Ban RB5154 Clubmaster",
        barcode="8901234567890",
        category_code="FRAME",
        brand_code="RAYBAN",
        mrp=Decimal("7490"),
        offer_price=Decimal("7490")
    )
    engine.products[frame.id] = frame
    
    contact_lens = Product(
        id="prod-002",
        sku="CV-MYDAY-90",
        name="CooperVision MyDay 90 Pack",
        barcode="8901234567891",
        category_code="CONTACT_LENS",
        brand_code="COOPERVISION",
        mrp=Decimal("4500"),
        offer_price=Decimal("4500"),
        track_expiry=True,
        track_batch=True
    )
    engine.products[contact_lens.id] = contact_lens
    
    # -------------------------------------------------------------------------
    # SCENARIO 1: Add stock from GRN
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 1: Receive Stock from Vendor (GRN)")
    print("-" * 50)
    
    success, msg, stock = engine.add_stock(
        product_id=frame.id,
        store_id="store-bv-001",
        quantity=5,
        user_id="user-catalog",
        movement_type=StockMovementType.PURCHASE_IN,
        reference_type="GRN",
        reference_id="GRN-001",
        notes="Initial stock from vendor"
    )
    print(f"Frame stock: {msg}")
    
    # Add contact lens with batch and expiry
    success, msg, stock = engine.add_stock(
        product_id=contact_lens.id,
        store_id="store-bv-001",
        quantity=10,
        user_id="user-catalog",
        movement_type=StockMovementType.PURCHASE_IN,
        reference_type="GRN",
        reference_id="GRN-001",
        batch_code="BATCH-2026-001",
        expiry_date=date(2026, 6, 30),
        notes="Contact lens with expiry tracking"
    )
    print(f"Contact lens stock: {msg}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 2: Accept stock and assign location
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 2: Store Manager Accepts Stock")
    print("-" * 50)
    
    # Get the stock unit
    for su in engine.stock_units.values():
        if su.product_id == frame.id:
            success, msg, barcode = engine.accept_stock(
                stock_unit_id=su.id,
                user_id="user-manager",
                location_code="C1",
                store_code="BV-BKR"
            )
            print(f"Frame accepted: {msg}")
            break
    
    # -------------------------------------------------------------------------
    # SCENARIO 3: Create stock transfer
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 3: Stock Transfer Between Stores")
    print("-" * 50)
    
    transfer = engine.create_transfer(
        from_store_id="store-bv-001",
        from_store_name="Better Vision - Bokaro",
        from_store_code="BV-BKR",
        to_store_id="store-bv-002",
        to_store_name="Better Vision - Ranchi",
        to_store_code="BV-RNC",
        user_id="user-area-mgr"
    )
    print(f"Transfer created: {transfer.transfer_number}")
    
    # Add item
    success, msg = engine.add_transfer_item(
        transfer_id=transfer.id,
        product_id=frame.id,
        product_name=frame.name,
        product_sku=frame.sku,
        quantity=2
    )
    print(f"Item added: {msg}")
    
    # Mark barcode removed (REQUIRED before sending)
    for item in transfer.items:
        engine.mark_barcode_removed(transfer.id, item.id, "user-staff")
    print("Barcodes removed from items")
    
    # Try to send without removing barcodes would fail
    success, msg = engine.send_transfer(
        transfer_id=transfer.id,
        user_id="user-area-mgr",
        user_name="Area Manager"
    )
    print(f"Transfer sent: {msg}")
    
    # Receive at destination
    received_quantities = {transfer.items[0].id: 2}
    success, msg = engine.receive_transfer(
        transfer_id=transfer.id,
        user_id="user-manager-2",
        user_name="Store Manager 2",
        received_quantities=received_quantities
    )
    print(f"Transfer received: {msg}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 4: Reserve stock for order
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 4: Reserve Stock for Order")
    print("-" * 50)
    
    success, msg = engine.reserve_stock(
        product_id=frame.id,
        store_id="store-bv-001",
        quantity=1,
        order_id="ORD-001",
        user_id="user-sales"
    )
    print(f"Reservation: {msg}")
    
    # Check available stock
    stock = engine.get_store_stock("store-bv-001")
    for item in stock:
        if item["product_id"] == frame.id:
            print(f"Stock status: Total={item['quantity']}, Reserved={item['reserved']}, Available={item['available']}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 5: Daily stock count
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 5: Daily Stock Count")
    print("-" * 50)
    
    count = engine.start_stock_count(
        store_id="store-bv-001",
        user_id="user-staff",
        user_name="Rahul Kumar"
    )
    print(f"Stock count started")
    
    # Record counts
    engine.record_count_item(
        count_id=count.id,
        product_id=frame.id,
        product_name=frame.name,
        system_quantity=3,  # Reduced due to transfer
        actual_quantity=3,  # Matches
        user_id="user-staff"
    )
    
    engine.record_count_item(
        count_id=count.id,
        product_id=contact_lens.id,
        product_name=contact_lens.name,
        system_quantity=10,
        actual_quantity=9,  # 1 missing!
        user_id="user-staff"
    )
    
    # Complete count
    success, msg = engine.complete_stock_count(
        count_id=count.id,
        user_id="user-manager",
        apply_adjustments=True
    )
    print(f"Stock count: {msg}")
    
    # -------------------------------------------------------------------------
    # SCENARIO 6: Check alerts
    # -------------------------------------------------------------------------
    print("\n📦 SCENARIO 6: Check Alerts")
    print("-" * 50)
    
    low_stock = engine.get_low_stock_alerts("store-bv-001")
    print(f"Low stock alerts: {len(low_stock)}")
    for alert in low_stock:
        print(f"  - {alert.product_name}: {alert.current_quantity} (min: {alert.minimum_quantity})")
    
    expiry_alerts = engine.get_expiry_alerts("store-bv-001")
    print(f"Expiry alerts: {len(expiry_alerts)}")
    for alert in expiry_alerts:
        print(f"  - {alert.product_name}: expires in {alert.days_to_expiry} days ({alert.expiry_date})")
    
    print("\n" + "=" * 70)
    print("END OF DEMO")
    print("=" * 70)


if __name__ == "__main__":
    demo_inventory()
