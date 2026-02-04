"""
IMS 2.0 - Repository Layer Test
================================
Test all repositories with mock database
"""
import sys
sys.path.insert(0, '/home/claude/ims-2.0-core/backend')

from database.connection import get_mock_db
from database.repositories.user_repository import UserRepository
from database.repositories.store_repository import StoreRepository
from database.repositories.product_repository import ProductRepository, StockRepository
from database.repositories.customer_repository import CustomerRepository
from database.repositories.order_repository import OrderRepository
from database.repositories.vendor_repository import VendorRepository
from database.repositories.task_repository import TaskRepository
from datetime import datetime, date


def test_repositories():
    print("=" * 60)
    print("IMS 2.0 REPOSITORY LAYER TEST")
    print("=" * 60)
    
    # Create mock database
    mock_db = get_mock_db()
    
    # =========================================================================
    # Test User Repository
    # =========================================================================
    print("\n👤 Testing User Repository")
    user_repo = UserRepository(mock_db["users"])
    
    # Create user
    user = user_repo.create({
        "username": "rahul.singh",
        "email": "rahul@bettervision.in",
        "full_name": "Rahul Singh",
        "roles": ["SALES_STAFF"],
        "store_ids": ["store-001"],
        "is_active": True
    })
    print(f"  Created: {user['username']}")
    
    # Find by username
    found = user_repo.find_by_username("rahul.singh")
    print(f"  Found by username: {found['full_name']}")
    
    # Add role
    user_repo.add_role(user["user_id"], "CASHIER")
    print(f"  Added role: CASHIER")
    
    # =========================================================================
    # Test Store Repository
    # =========================================================================
    print("\n🏪 Testing Store Repository")
    store_repo = StoreRepository(mock_db["stores"])
    
    store = store_repo.create({
        "store_code": "BKR",
        "store_name": "Better Vision Bokaro",
        "brand": "BETTER_VISION",
        "city": "Bokaro",
        "state": "Jharkhand",
        "is_active": True,
        "enabled_categories": ["FRAME", "SUNGLASS", "READING_GLASSES", "OPTICAL_LENS",
                                "CONTACT_LENS", "COLORED_CONTACT_LENS", "WATCH", "SMARTWATCH",
                                "SMARTGLASSES", "WALL_CLOCK", "ACCESSORIES", "SERVICES"]
    })
    print(f"  Created: {store['store_name']}")
    
    # Check category
    has_frame = store_repo.has_category(store["store_id"], "FRAME")
    print(f"  Has FRAME category: {has_frame}")
    
    # =========================================================================
    # Test Product Repository
    # =========================================================================
    print("\n📦 Testing Product Repository")
    product_repo = ProductRepository(mock_db["products"])
    
    product = product_repo.create({
        "sku": "RB-5154-BLK-49",
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB5154",
        "color": "Black",
        "size": "49-21-140",
        "mrp": 8500,
        "offer_price": 8500,
        "is_active": True,
        "is_discountable": True
    })
    print(f"  Created: {product['brand']} {product['model']}")
    
    # Find by SKU
    found = product_repo.find_by_sku("RB-5154-BLK-49")
    print(f"  Found by SKU: {found['brand']}")
    
    # =========================================================================
    # Test Customer Repository
    # =========================================================================
    print("\n👥 Testing Customer Repository")
    customer_repo = CustomerRepository(mock_db["customers"])
    
    customer = customer_repo.create({
        "customer_type": "B2C",
        "name": "Rajesh Kumar",
        "mobile": "9123456789",
        "email": "rajesh@email.com",
        "home_store_id": "store-001",
        "loyalty_points": 0
    })
    print(f"  Created: {customer['name']}")
    
    # Add patient
    customer_repo.add_patient(customer["customer_id"], {
        "patient_id": "patient-001",
        "name": "Rajesh Kumar",
        "mobile": "9123456789"
    })
    print(f"  Added patient")
    
    # Add loyalty
    customer_repo.add_loyalty_points(customer["customer_id"], 100)
    print(f"  Added 100 loyalty points")
    
    # =========================================================================
    # Test Order Repository
    # =========================================================================
    print("\n🛒 Testing Order Repository")
    order_repo = OrderRepository(mock_db["orders"])
    
    order = order_repo.create({
        "order_number": "BV-BKR-2024-0001",
        "customer_id": customer["customer_id"],
        "store_id": "store-001",
        "salesperson_id": user["user_id"],
        "items": [{
            "item_id": "item-001",
            "product_id": product["product_id"],
            "product_name": "Ray-Ban RB5154",
            "quantity": 1,
            "unit_price": 8500,
            "total": 8500
        }],
        "grand_total": 8500,
        "amount_paid": 0,
        "balance_due": 8500,
        "status": "CONFIRMED",
        "payment_status": "UNPAID"
    })
    print(f"  Created: {order['order_number']}")
    
    # Add payment
    order_repo.add_payment(order["order_id"], {
        "payment_id": "pay-001",
        "method": "CASH",
        "amount": 5000,
        "received_at": datetime.now()
    })
    print(f"  Added payment: ₹5000")
    
    # Update status
    order_repo.update_status(order["order_id"], "READY")
    print(f"  Updated status to READY")
    
    # =========================================================================
    # Test Vendor Repository
    # =========================================================================
    print("\n🏭 Testing Vendor Repository")
    vendor_repo = VendorRepository(mock_db["vendors"])
    
    vendor = vendor_repo.create({
        "vendor_code": "VND-001",
        "legal_name": "Essilor India Pvt Ltd",
        "trade_name": "Essilor",
        "vendor_type": "INDIAN",
        "gstin_status": "REGISTERED",
        "gstin": "27AABCE1234F1ZP",
        "is_active": True
    })
    print(f"  Created: {vendor['trade_name']}")
    
    # =========================================================================
    # Test Task Repository
    # =========================================================================
    print("\n📋 Testing Task Repository")
    task_repo = TaskRepository(mock_db["tasks"])
    
    task = task_repo.create({
        "task_number": "TSK-2024-0001",
        "title": "Stock Count - Frame Section",
        "category": "STOCK",
        "priority": "P2",
        "source": "SOP",
        "assigned_to": user["user_id"],
        "store_id": "store-001",
        "due_at": datetime.now(),
        "status": "OPEN"
    })
    print(f"  Created: {task['title']}")
    
    # Start task
    task_repo.start_task(task["task_id"])
    print(f"  Started task")
    
    # Complete task
    task_repo.complete_task(task["task_id"], "Completed stock count")
    print(f"  Completed task")
    
    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 60)
    print("✅ ALL REPOSITORY TESTS PASSED")
    print("=" * 60)
    
    print(f"\n📊 Summary:")
    print(f"  Users: {user_repo.count()}")
    print(f"  Stores: {store_repo.count()}")
    print(f"  Products: {product_repo.count()}")
    print(f"  Customers: {customer_repo.count()}")
    print(f"  Orders: {order_repo.count()}")
    print(f"  Vendors: {vendor_repo.count()}")
    print(f"  Tasks: {task_repo.count()}")


if __name__ == "__main__":
    test_repositories()
