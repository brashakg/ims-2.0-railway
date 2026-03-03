"""
IMS 2.0 - Database Seed Data
============================
Sample data for demo/development mode
"""
from datetime import datetime, timedelta
import uuid

# ============================================================================
# STORES
# ============================================================================
STORES = [
    {
        "_id": "store-001",
        "store_id": "store-001",
        "name": "Better Vision - Connaught Place",
        "code": "BV-CP",
        "address": "123 Vision Street, Connaught Place",
        "city": "New Delhi",
        "state": "Delhi",
        "pincode": "110001",
        "phone": "+91 11 4567 8900",
        "email": "cp@bettervision.in",
        "gst_number": "07AABCT1234Q1ZP",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "store-002",
        "store_id": "store-002",
        "name": "Better Vision - South Extension",
        "code": "BV-SE",
        "address": "45 Market Road, South Extension",
        "city": "New Delhi",
        "state": "Delhi",
        "pincode": "110049",
        "phone": "+91 11 4567 8901",
        "email": "se@bettervision.in",
        "gst_number": "07AABCT1234Q2ZP",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    }
]

# ============================================================================
# USERS
# ============================================================================
USERS = [
    {
        "_id": "user-001",
        "user_id": "user-001",
        "username": "admin",
        "password_hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.rP1zLjHZs8HPXW",  # admin123
        "full_name": "System Administrator",
        "email": "admin@bettervision.in",
        "phone": "9999999999",
        "roles": ["SUPERADMIN"],
        "store_ids": ["store-001", "store-002"],
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "user-002",
        "user_id": "user-002",
        "username": "store_manager",
        "password_hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.rP1zLjHZs8HPXW",  # admin123
        "full_name": "Rajesh Kumar",
        "email": "rajesh@bettervision.in",
        "phone": "9876543210",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["store-001"],
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "user-003",
        "user_id": "user-003",
        "username": "sales_staff",
        "password_hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.rP1zLjHZs8HPXW",
        "full_name": "Neha Gupta",
        "email": "neha@bettervision.in",
        "phone": "9876543211",
        "roles": ["SALES_STAFF"],
        "store_ids": ["store-001"],
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "user-004",
        "user_id": "user-004",
        "username": "optometrist",
        "password_hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.rP1zLjHZs8HPXW",
        "full_name": "Dr. Amit Sharma",
        "email": "amit@bettervision.in",
        "phone": "9876543212",
        "roles": ["OPTOMETRIST"],
        "store_ids": ["store-001"],
        "is_active": True,
        "created_at": datetime.now().isoformat()
    }
]

# ============================================================================
# CUSTOMERS
# ============================================================================
CUSTOMERS = [
    {
        "_id": "cust-001",
        "customer_id": "cust-001",
        "customer_type": "B2C",
        "name": "Rahul Sharma",
        "mobile": "9876543210",
        "email": "rahul.sharma@email.com",
        "home_store_id": "store-001",
        "loyalty_points": 1250,
        "store_credit": 500,
        "total_purchases": 45230,
        "is_active": True,
        "patients": [
            {"patient_id": "pat-001", "name": "Rahul Sharma", "relation": "Self", "dob": "1989-05-15"},
            {"patient_id": "pat-002", "name": "Priya Sharma", "relation": "Wife", "dob": "1992-08-22"}
        ],
        "created_at": "2024-01-15T10:30:00Z"
    },
    {
        "_id": "cust-002",
        "customer_id": "cust-002",
        "customer_type": "B2C",
        "name": "Anita Verma",
        "mobile": "9876543211",
        "email": "anita.verma@email.com",
        "home_store_id": "store-001",
        "loyalty_points": 850,
        "store_credit": 0,
        "total_purchases": 32100,
        "is_active": True,
        "patients": [
            {"patient_id": "pat-003", "name": "Anita Verma", "relation": "Self", "dob": "1982-03-10"}
        ],
        "created_at": "2024-02-20T14:15:00Z"
    },
    {
        "_id": "cust-003",
        "customer_id": "cust-003",
        "customer_type": "B2B",
        "name": "Vision Care Hospital",
        "mobile": "9876543212",
        "email": "procurement@visioncare.com",
        "gstin": "07AABCT1234Q1ZP",
        "home_store_id": "store-001",
        "loyalty_points": 0,
        "store_credit": 0,
        "total_purchases": 245000,
        "is_active": True,
        "patients": [],
        "created_at": "2024-01-05T09:00:00Z"
    },
    {
        "_id": "cust-004",
        "customer_id": "cust-004",
        "customer_type": "B2C",
        "name": "Vikram Singh",
        "mobile": "9876543213",
        "email": "vikram.singh@email.com",
        "home_store_id": "store-001",
        "loyalty_points": 2100,
        "store_credit": 1000,
        "total_purchases": 78500,
        "is_active": True,
        "patients": [
            {"patient_id": "pat-004", "name": "Vikram Singh", "relation": "Self", "dob": "1969-11-20"},
            {"patient_id": "pat-005", "name": "Sunita Singh", "relation": "Wife", "dob": "1972-07-14"},
            {"patient_id": "pat-006", "name": "Arjun Singh", "relation": "Son", "dob": "1999-02-28"}
        ],
        "created_at": "2023-11-10T11:45:00Z"
    },
    {
        "_id": "cust-005",
        "customer_id": "cust-005",
        "customer_type": "B2C",
        "name": "Meera Patel",
        "mobile": "9876543214",
        "email": "meera.patel@email.com",
        "home_store_id": "store-001",
        "loyalty_points": 450,
        "store_credit": 200,
        "total_purchases": 15600,
        "is_active": True,
        "patients": [
            {"patient_id": "pat-007", "name": "Meera Patel", "relation": "Self", "dob": "1996-12-05"}
        ],
        "created_at": "2024-03-01T16:20:00Z"
    }
]

# ============================================================================
# PRODUCTS (Frames, Lenses, etc.)
# ============================================================================
PRODUCTS = [
    # Frames
    {
        "_id": "prod-fr-001",
        "product_id": "prod-fr-001",
        "sku": "BV-FR-RAY-001",
        "name": "Ray-Ban Aviator Classic",
        "brand": "Ray-Ban",
        "category": "FR",
        "subcategory": "Aviator",
        "mrp": 8990,
        "cost_price": 5500,
        "offer_price": 7990,
        "hsn_code": "9003",
        "gst_rate": 18,
        "frame_type": "Full Rim",
        "frame_shape": "Aviator",
        "frame_material": "Metal",
        "frame_color": "Gold",
        "frame_size": "58-14-140",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "prod-fr-002",
        "product_id": "prod-fr-002",
        "sku": "BV-FR-OAK-001",
        "name": "Oakley Holbrook",
        "brand": "Oakley",
        "category": "FR",
        "subcategory": "Rectangle",
        "mrp": 12500,
        "cost_price": 7500,
        "offer_price": 10990,
        "hsn_code": "9003",
        "gst_rate": 18,
        "frame_type": "Full Rim",
        "frame_shape": "Rectangle",
        "frame_material": "Acetate",
        "frame_color": "Matte Black",
        "frame_size": "55-18-137",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "prod-fr-003",
        "product_id": "prod-fr-003",
        "sku": "BV-FR-VGE-001",
        "name": "Vogue VO5286",
        "brand": "Vogue",
        "category": "FR",
        "subcategory": "Cat Eye",
        "mrp": 6990,
        "cost_price": 4200,
        "offer_price": 5990,
        "hsn_code": "9003",
        "gst_rate": 18,
        "frame_type": "Full Rim",
        "frame_shape": "Cat Eye",
        "frame_material": "Acetate",
        "frame_color": "Tortoise",
        "frame_size": "53-17-140",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    # Sunglasses
    {
        "_id": "prod-sg-001",
        "product_id": "prod-sg-001",
        "sku": "BV-SG-RAY-001",
        "name": "Ray-Ban Wayfarer Sunglasses",
        "brand": "Ray-Ban",
        "category": "SG",
        "subcategory": "Wayfarer",
        "mrp": 9990,
        "cost_price": 6000,
        "offer_price": 8990,
        "hsn_code": "9004",
        "gst_rate": 18,
        "frame_type": "Full Rim",
        "frame_shape": "Square",
        "frame_material": "Acetate",
        "frame_color": "Black",
        "lens_color": "Green",
        "uv_protection": "100%",
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    # Lenses
    {
        "_id": "prod-ls-001",
        "product_id": "prod-ls-001",
        "sku": "BV-LS-ESS-001",
        "name": "Essilor Crizal Sapphire",
        "brand": "Essilor",
        "category": "LS",
        "subcategory": "Single Vision",
        "mrp": 4500,
        "cost_price": 2800,
        "offer_price": 3990,
        "hsn_code": "9001",
        "gst_rate": 12,
        "lens_type": "Single Vision",
        "lens_material": "Polycarbonate",
        "coating": "Anti-Reflective",
        "index": 1.59,
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "prod-ls-002",
        "product_id": "prod-ls-002",
        "sku": "BV-LS-ZEI-001",
        "name": "Zeiss Progressive Individual",
        "brand": "Zeiss",
        "category": "LS",
        "subcategory": "Progressive",
        "mrp": 18000,
        "cost_price": 11000,
        "offer_price": 15990,
        "hsn_code": "9001",
        "gst_rate": 12,
        "lens_type": "Progressive",
        "lens_material": "High Index",
        "coating": "DuraVision Platinum",
        "index": 1.67,
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    # Contact Lenses
    {
        "_id": "prod-cl-001",
        "product_id": "prod-cl-001",
        "sku": "BV-CL-ACU-001",
        "name": "Acuvue Oasys Daily (30 pack)",
        "brand": "Acuvue",
        "category": "CL",
        "subcategory": "Daily Disposable",
        "mrp": 2200,
        "cost_price": 1400,
        "offer_price": 1990,
        "hsn_code": "9001",
        "gst_rate": 12,
        "lens_type": "Spherical",
        "wear_duration": "Daily",
        "pack_size": 30,
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    # Accessories
    {
        "_id": "prod-acc-001",
        "product_id": "prod-acc-001",
        "sku": "BV-ACC-CLN-001",
        "name": "Premium Lens Cleaning Kit",
        "brand": "Better Vision",
        "category": "ACC",
        "subcategory": "Cleaning",
        "mrp": 350,
        "cost_price": 150,
        "offer_price": 299,
        "hsn_code": "3402",
        "gst_rate": 18,
        "is_active": True,
        "created_at": datetime.now().isoformat()
    },
    {
        "_id": "prod-acc-002",
        "product_id": "prod-acc-002",
        "sku": "BV-ACC-CSE-001",
        "name": "Hard Shell Glasses Case",
        "brand": "Better Vision",
        "category": "ACC",
        "subcategory": "Cases",
        "mrp": 499,
        "cost_price": 200,
        "offer_price": 399,
        "hsn_code": "4202",
        "gst_rate": 18,
        "is_active": True,
        "created_at": datetime.now().isoformat()
    }
]

# ============================================================================
# STOCK UNITS
# ============================================================================
def generate_stock():
    stock = []
    for product in PRODUCTS:
        for store in STORES:
            stock.append({
                "_id": f"stock-{product['product_id']}-{store['store_id']}",
                "stock_id": f"stock-{product['product_id']}-{store['store_id']}",
                "product_id": product["product_id"],
                "store_id": store["store_id"],
                "sku": product["sku"],
                "name": product["name"],
                "brand": product["brand"],
                "category": product["category"],
                "mrp": product["mrp"],
                "offer_price": product.get("offer_price", product["mrp"]),
                "quantity": 25 if product["category"] in ["FR", "SG"] else 50,
                "reserved": 2 if product["category"] in ["FR", "SG"] else 5,
                "min_stock": 5,
                "location": f"A{ord(product['category'][0]) % 10}-{(hash(product['product_id']) % 20) + 1}",
                "last_updated": datetime.now().isoformat()
            })
    return stock

STOCK_UNITS = generate_stock()

# ============================================================================
# ORDERS
# ============================================================================
now = datetime.now()
ORDERS = [
    {
        "_id": "ord-001",
        "order_id": "ord-001",
        "order_number": "BV-CP-2024-001542",
        "store_id": "store-001",
        "customer_id": "cust-001",
        "customer_name": "Rahul Sharma",
        "customer_phone": "9876543210",
        "patient_id": "pat-001",
        "patient_name": "Rahul Sharma",
        "salesperson_id": "user-003",
        "items": [
            {
                "item_id": "item-001",
                "item_type": "FRAME",
                "product_id": "prod-fr-001",
                "product_name": "Ray-Ban Aviator Classic",
                "quantity": 1,
                "unit_price": 7990,
                "discount_percent": 10,
                "discount_amount": 799,
                "item_total": 7191
            },
            {
                "item_id": "item-002",
                "item_type": "LENS",
                "product_id": "prod-ls-001",
                "product_name": "Essilor Crizal Sapphire",
                "quantity": 2,
                "unit_price": 3990,
                "discount_percent": 5,
                "discount_amount": 399,
                "item_total": 7581
            }
        ],
        "subtotal": 14772,
        "total_discount": 1198,
        "tax_rate": 18,
        "tax_amount": 2659,
        "grand_total": 17431,
        "amount_paid": 10000,
        "balance_due": 7431,
        "payment_status": "PARTIAL",
        "status": "IN_PROGRESS",
        "payments": [
            {
                "payment_id": "pay-001",
                "method": "CASH",
                "amount": 5000,
                "received_by": "user-003",
                "received_at": (now - timedelta(days=2)).isoformat()
            },
            {
                "payment_id": "pay-002",
                "method": "UPI",
                "amount": 5000,
                "reference": "UPI123456789",
                "received_by": "user-003",
                "received_at": (now - timedelta(days=1)).isoformat()
            }
        ],
        "expected_delivery": (now + timedelta(days=5)).isoformat(),
        "notes": "Customer prefers thin lenses",
        "created_at": (now - timedelta(days=3)).isoformat(),
        "created_by": "user-003"
    },
    {
        "_id": "ord-002",
        "order_id": "ord-002",
        "order_number": "BV-CP-2024-001543",
        "store_id": "store-001",
        "customer_id": "cust-002",
        "customer_name": "Anita Verma",
        "customer_phone": "9876543211",
        "patient_id": "pat-003",
        "patient_name": "Anita Verma",
        "salesperson_id": "user-003",
        "items": [
            {
                "item_id": "item-003",
                "item_type": "FRAME",
                "product_id": "prod-fr-003",
                "product_name": "Vogue VO5286",
                "quantity": 1,
                "unit_price": 5990,
                "discount_percent": 0,
                "discount_amount": 0,
                "item_total": 5990
            },
            {
                "item_id": "item-004",
                "item_type": "LENS",
                "product_id": "prod-ls-002",
                "product_name": "Zeiss Progressive Individual",
                "quantity": 2,
                "unit_price": 15990,
                "discount_percent": 10,
                "discount_amount": 3198,
                "item_total": 28782
            }
        ],
        "subtotal": 34772,
        "total_discount": 3198,
        "tax_rate": 18,
        "tax_amount": 5683,
        "grand_total": 40455,
        "amount_paid": 40455,
        "balance_due": 0,
        "payment_status": "PAID",
        "status": "READY",
        "payments": [
            {
                "payment_id": "pay-003",
                "method": "CARD",
                "amount": 40455,
                "reference": "CARD9876543210",
                "received_by": "user-003",
                "received_at": (now - timedelta(days=1)).isoformat()
            }
        ],
        "expected_delivery": now.isoformat(),
        "created_at": (now - timedelta(days=5)).isoformat(),
        "created_by": "user-003"
    },
    {
        "_id": "ord-003",
        "order_id": "ord-003",
        "order_number": "BV-CP-2024-001544",
        "store_id": "store-001",
        "customer_id": "cust-004",
        "customer_name": "Vikram Singh",
        "customer_phone": "9876543213",
        "patient_id": "pat-004",
        "patient_name": "Vikram Singh",
        "salesperson_id": "user-002",
        "items": [
            {
                "item_id": "item-005",
                "item_type": "SUNGLASSES",
                "product_id": "prod-sg-001",
                "product_name": "Ray-Ban Wayfarer Sunglasses",
                "quantity": 1,
                "unit_price": 8990,
                "discount_percent": 15,
                "discount_amount": 1349,
                "item_total": 7641
            }
        ],
        "subtotal": 7641,
        "total_discount": 1349,
        "tax_rate": 18,
        "tax_amount": 1375,
        "grand_total": 9016,
        "amount_paid": 9016,
        "balance_due": 0,
        "payment_status": "PAID",
        "status": "DELIVERED",
        "payments": [
            {
                "payment_id": "pay-004",
                "method": "UPI",
                "amount": 9016,
                "reference": "UPI987654321",
                "received_by": "user-002",
                "received_at": (now - timedelta(days=3)).isoformat()
            }
        ],
        "expected_delivery": (now - timedelta(days=2)).isoformat(),
        "delivered_at": (now - timedelta(days=2)).isoformat(),
        "created_at": (now - timedelta(days=4)).isoformat(),
        "created_by": "user-002"
    },
    {
        "_id": "ord-004",
        "order_id": "ord-004",
        "order_number": "BV-CP-2024-001545",
        "store_id": "store-001",
        "customer_id": "cust-005",
        "customer_name": "Meera Patel",
        "customer_phone": "9876543214",
        "patient_id": "pat-007",
        "patient_name": "Meera Patel",
        "salesperson_id": "user-003",
        "items": [
            {
                "item_id": "item-006",
                "item_type": "CONTACT_LENS",
                "product_id": "prod-cl-001",
                "product_name": "Acuvue Oasys Daily (30 pack)",
                "quantity": 2,
                "unit_price": 1990,
                "discount_percent": 0,
                "discount_amount": 0,
                "item_total": 3980
            },
            {
                "item_id": "item-007",
                "item_type": "ACCESSORY",
                "product_id": "prod-acc-001",
                "product_name": "Premium Lens Cleaning Kit",
                "quantity": 1,
                "unit_price": 299,
                "discount_percent": 0,
                "discount_amount": 0,
                "item_total": 299
            }
        ],
        "subtotal": 4279,
        "total_discount": 0,
        "tax_rate": 12,
        "tax_amount": 513,
        "grand_total": 4792,
        "amount_paid": 0,
        "balance_due": 4792,
        "payment_status": "PENDING",
        "status": "CONFIRMED",
        "payments": [],
        "expected_delivery": (now + timedelta(days=1)).isoformat(),
        "created_at": now.isoformat(),
        "created_by": "user-003"
    }
]

# ============================================================================
# WORKSHOP JOBS
# ============================================================================
WORKSHOP_JOBS = [
    {
        "_id": "job-001",
        "job_id": "job-001",
        "job_number": "WS-240205-A1B2C3",
        "order_id": "ord-001",
        "order_number": "BV-CP-2024-001542",
        "store_id": "store-001",
        "customer_id": "cust-001",
        "customer_name": "Rahul Sharma",
        "customer_phone": "9876543210",
        "frame_name": "Ray-Ban Aviator Classic",
        "frame_barcode": "BV-FR-RAY-001",
        "lens_type": "Single Vision - Essilor Crizal",
        "prescription_id": "rx-001",
        "status": "IN_PROGRESS",
        "priority": "NORMAL",
        "technician_id": "user-004",
        "fitting_instructions": "Standard fitting",
        "special_notes": "Customer prefers thin lenses",
        "expected_date": (now + timedelta(days=3)).isoformat(),
        "promised_date": (now + timedelta(days=5)).isoformat(),
        "created_at": (now - timedelta(days=3)).isoformat(),
        "created_by": "user-003"
    },
    {
        "_id": "job-002",
        "job_id": "job-002",
        "job_number": "WS-240205-D4E5F6",
        "order_id": "ord-002",
        "order_number": "BV-CP-2024-001543",
        "store_id": "store-001",
        "customer_id": "cust-002",
        "customer_name": "Anita Verma",
        "customer_phone": "9876543211",
        "frame_name": "Vogue VO5286",
        "frame_barcode": "BV-FR-VGE-001",
        "lens_type": "Progressive - Zeiss Individual",
        "prescription_id": "rx-002",
        "status": "READY",
        "priority": "EXPRESS",
        "technician_id": "user-004",
        "fitting_instructions": "Adjust temple length",
        "special_notes": "High-value progressive order",
        "expected_date": (now - timedelta(days=1)).isoformat(),
        "promised_date": now.isoformat(),
        "completed_at": (now - timedelta(hours=2)).isoformat(),
        "created_at": (now - timedelta(days=5)).isoformat(),
        "created_by": "user-003"
    }
]

# ============================================================================
# PRESCRIPTIONS
# ============================================================================
PRESCRIPTIONS = [
    {
        "_id": "rx-001",
        "prescription_id": "rx-001",
        "customer_id": "cust-001",
        "patient_id": "pat-001",
        "patient_name": "Rahul Sharma",
        "optometrist_id": "user-004",
        "optometrist_name": "Dr. Amit Sharma",
        "prescription_date": (now - timedelta(days=3)).isoformat(),
        "expiry_date": (now + timedelta(days=365)).isoformat(),
        "right_eye": {
            "sphere": -2.50,
            "cylinder": -0.75,
            "axis": 180,
            "add": 0,
            "pd": 32
        },
        "left_eye": {
            "sphere": -2.25,
            "cylinder": -0.50,
            "axis": 175,
            "add": 0,
            "pd": 32
        },
        "recommendations": "Single vision lenses recommended. Anti-reflective coating suggested.",
        "notes": "First prescription",
        "is_validated": True,
        "created_at": (now - timedelta(days=3)).isoformat()
    },
    {
        "_id": "rx-002",
        "prescription_id": "rx-002",
        "customer_id": "cust-002",
        "patient_id": "pat-003",
        "patient_name": "Anita Verma",
        "optometrist_id": "user-004",
        "optometrist_name": "Dr. Amit Sharma",
        "prescription_date": (now - timedelta(days=5)).isoformat(),
        "expiry_date": (now + timedelta(days=365)).isoformat(),
        "right_eye": {
            "sphere": -1.75,
            "cylinder": -0.25,
            "axis": 90,
            "add": 2.00,
            "pd": 31
        },
        "left_eye": {
            "sphere": -2.00,
            "cylinder": -0.50,
            "axis": 85,
            "add": 2.00,
            "pd": 31
        },
        "recommendations": "Progressive lenses recommended for near and distance vision.",
        "notes": "Reading difficulty reported",
        "is_validated": True,
        "created_at": (now - timedelta(days=5)).isoformat()
    }
]

# ============================================================================
# TASKS
# ============================================================================
TASKS = [
    {
        "_id": "task-001",
        "task_id": "task-001",
        "title": "Follow up with Rahul Sharma",
        "description": "Call customer about balance payment of Rs. 7,431",
        "task_type": "FOLLOW_UP",
        "priority": "HIGH",
        "status": "PENDING",
        "assigned_to": "user-003",
        "assigned_by": "user-002",
        "related_entity_type": "ORDER",
        "related_entity_id": "ord-001",
        "due_date": (now + timedelta(days=1)).isoformat(),
        "store_id": "store-001",
        "created_at": now.isoformat()
    },
    {
        "_id": "task-002",
        "task_id": "task-002",
        "title": "Deliver order to Anita Verma",
        "description": "Order BV-CP-2024-001543 is ready for delivery",
        "task_type": "DELIVERY",
        "priority": "NORMAL",
        "status": "IN_PROGRESS",
        "assigned_to": "user-003",
        "assigned_by": "user-002",
        "related_entity_type": "ORDER",
        "related_entity_id": "ord-002",
        "due_date": now.isoformat(),
        "store_id": "store-001",
        "created_at": (now - timedelta(days=1)).isoformat()
    },
    {
        "_id": "task-003",
        "task_id": "task-003",
        "title": "Reorder Ray-Ban Aviator stock",
        "description": "Stock running low - only 5 units left",
        "task_type": "INVENTORY",
        "priority": "NORMAL",
        "status": "PENDING",
        "assigned_to": "user-002",
        "assigned_by": "user-001",
        "related_entity_type": "PRODUCT",
        "related_entity_id": "prod-fr-001",
        "due_date": (now + timedelta(days=3)).isoformat(),
        "store_id": "store-001",
        "created_at": (now - timedelta(days=2)).isoformat()
    }
]

# ============================================================================
# EYE TEST QUEUE (Clinical)
# ============================================================================
QUEUE_ITEMS = [
    {
        "_id": "q-001",
        "queue_id": "q-001",
        "token_number": "T001",
        "store_id": "store-001",
        "customer_id": "cust-001",
        "patient_id": "pat-001",
        "patient_name": "Rahul Sharma",
        "customer_phone": "9876543210",
        "age": 35,
        "reason": "Routine checkup",
        "status": "WAITING",
        "created_at": (now - timedelta(minutes=45)).isoformat(),
        "wait_time": 45
    },
    {
        "_id": "q-002",
        "queue_id": "q-002",
        "token_number": "T002",
        "store_id": "store-001",
        "customer_id": "cust-004",
        "patient_id": "pat-005",
        "patient_name": "Sunita Singh",
        "customer_phone": "9876543213",
        "age": 52,
        "reason": "Progressive lens consultation",
        "status": "IN_PROGRESS",
        "optometrist_id": "user-004",
        "started_at": (now - timedelta(minutes=15)).isoformat(),
        "created_at": (now - timedelta(minutes=30)).isoformat(),
        "wait_time": 15
    },
    {
        "_id": "q-003",
        "queue_id": "q-003",
        "token_number": "T003",
        "store_id": "store-001",
        "customer_id": "cust-005",
        "patient_id": "pat-007",
        "patient_name": "Meera Patel",
        "customer_phone": "9876543214",
        "age": 28,
        "reason": "Contact lens fitting",
        "status": "WAITING",
        "created_at": (now - timedelta(minutes=10)).isoformat(),
        "wait_time": 10
    }
]

# ============================================================================
# DAILY SALES RECORDS (for analytics)
# ============================================================================
def generate_daily_sales():
    """Generate 30 days of sales data for analytics"""
    sales = []
    import random
    random.seed(42)  # Consistent data

    for i in range(30):
        day = now - timedelta(days=29-i)
        day_str = day.strftime("%Y-%m-%d")

        # Weekend has lower sales
        is_weekend = day.weekday() >= 5
        base_sales = 45000 if is_weekend else 85000
        variance = random.uniform(0.7, 1.3)

        daily_revenue = round(base_sales * variance)
        order_count = random.randint(8, 25) if not is_weekend else random.randint(4, 12)
        avg_order_value = round(daily_revenue / order_count) if order_count > 0 else 0

        # Category breakdown
        frame_sales = round(daily_revenue * random.uniform(0.35, 0.45))
        lens_sales = round(daily_revenue * random.uniform(0.30, 0.40))
        sunglass_sales = round(daily_revenue * random.uniform(0.10, 0.15))
        accessory_sales = daily_revenue - frame_sales - lens_sales - sunglass_sales

        sales.append({
            "_id": f"sales-{day_str}",
            "date": day_str,
            "store_id": "store-001",
            "revenue": daily_revenue,
            "order_count": order_count,
            "avg_order_value": avg_order_value,
            "category_breakdown": {
                "frames": frame_sales,
                "lenses": lens_sales,
                "sunglasses": sunglass_sales,
                "accessories": accessory_sales
            },
            "payment_methods": {
                "cash": round(daily_revenue * random.uniform(0.25, 0.35)),
                "card": round(daily_revenue * random.uniform(0.30, 0.40)),
                "upi": round(daily_revenue * random.uniform(0.25, 0.35))
            },
            "customers_served": random.randint(order_count, order_count + 5),
            "new_customers": random.randint(1, 5),
            "eye_tests_conducted": random.randint(3, 10),
            "conversion_rate": round(random.uniform(0.55, 0.75), 2),
            "created_at": day.isoformat()
        })

    return sales

DAILY_SALES = generate_daily_sales()

# ============================================================================
# STAFF ATTENDANCE (for HR analytics)
# ============================================================================
def generate_attendance():
    """Generate attendance records for the past 30 days"""
    attendance = []
    import random
    random.seed(43)

    staff_members = [
        {"user_id": "user-002", "name": "Rajesh Kumar", "role": "STORE_MANAGER"},
        {"user_id": "user-003", "name": "Neha Gupta", "role": "SALES_STAFF"},
        {"user_id": "user-004", "name": "Dr. Amit Sharma", "role": "OPTOMETRIST"}
    ]

    for i in range(30):
        day = now - timedelta(days=29-i)
        day_str = day.strftime("%Y-%m-%d")
        is_weekend = day.weekday() >= 5

        for staff in staff_members:
            # 90% attendance rate, lower on weekends
            present = random.random() < (0.7 if is_weekend else 0.92)

            if present:
                check_in = day.replace(hour=9, minute=random.randint(0, 30))
                check_out = day.replace(hour=18, minute=random.randint(0, 45))
                hours_worked = round((check_out - check_in).seconds / 3600, 1)

                # Sales performance for sales staff
                sales_amount = 0
                if staff["role"] in ["SALES_STAFF", "STORE_MANAGER"]:
                    sales_amount = random.randint(15000, 45000)

                attendance.append({
                    "_id": f"att-{staff['user_id']}-{day_str}",
                    "user_id": staff["user_id"],
                    "user_name": staff["name"],
                    "role": staff["role"],
                    "store_id": "store-001",
                    "date": day_str,
                    "status": "PRESENT",
                    "check_in": check_in.isoformat(),
                    "check_out": check_out.isoformat(),
                    "hours_worked": hours_worked,
                    "sales_amount": sales_amount,
                    "created_at": check_in.isoformat()
                })
            else:
                attendance.append({
                    "_id": f"att-{staff['user_id']}-{day_str}",
                    "user_id": staff["user_id"],
                    "user_name": staff["name"],
                    "role": staff["role"],
                    "store_id": "store-001",
                    "date": day_str,
                    "status": "ABSENT",
                    "leave_type": random.choice(["CASUAL", "SICK", None]),
                    "created_at": day.isoformat()
                })

    return attendance

ATTENDANCE = generate_attendance()

# ============================================================================
# BUSINESS METRICS (KPIs)
# ============================================================================
BUSINESS_METRICS = [
    {
        "_id": "metrics-store-001",
        "store_id": "store-001",
        "period": "current_month",
        "revenue": {
            "total": 2145000,
            "target": 2500000,
            "achievement_percent": 85.8,
            "growth_vs_last_month": 12.5,
            "growth_vs_last_year": 18.2
        },
        "orders": {
            "total": 342,
            "completed": 298,
            "pending": 28,
            "cancelled": 16,
            "avg_value": 6272
        },
        "inventory": {
            "total_value": 4850000,
            "total_items": 1245,
            "low_stock_items": 23,
            "out_of_stock_items": 5,
            "expiring_soon": 12,
            "turnover_rate": 4.2
        },
        "customers": {
            "total_active": 856,
            "new_this_month": 67,
            "repeat_rate": 0.42,
            "avg_lifetime_value": 18500,
            "nps_score": 72
        },
        "staff": {
            "total_employees": 8,
            "present_today": 7,
            "avg_attendance_rate": 0.91,
            "top_performer": "Neha Gupta",
            "top_performer_sales": 485000
        },
        "clinical": {
            "eye_tests_month": 156,
            "prescriptions_issued": 142,
            "conversion_rate": 0.68,
            "avg_wait_time_mins": 18
        },
        "updated_at": now.isoformat()
    }
]

# ============================================================================
# CUSTOMER SEGMENTS (for analytics)
# ============================================================================
CUSTOMER_SEGMENTS = [
    {
        "_id": "seg-vip",
        "segment_id": "seg-vip",
        "name": "VIP Customers",
        "description": "High-value repeat customers",
        "criteria": {"total_purchases_min": 50000, "visits_min": 3},
        "customer_count": 45,
        "total_revenue": 3250000,
        "avg_order_value": 12500,
        "characteristics": ["Premium brands preferred", "Progressive lenses", "Multiple prescriptions"]
    },
    {
        "_id": "seg-regular",
        "segment_id": "seg-regular",
        "name": "Regular Customers",
        "description": "Consistent repeat buyers",
        "criteria": {"total_purchases_min": 15000, "visits_min": 2},
        "customer_count": 180,
        "total_revenue": 4850000,
        "avg_order_value": 6800,
        "characteristics": ["Mix of brands", "Single vision dominant", "Price sensitive"]
    },
    {
        "_id": "seg-new",
        "segment_id": "seg-new",
        "name": "New Customers",
        "description": "First-time buyers",
        "criteria": {"visits_max": 1},
        "customer_count": 320,
        "total_revenue": 2100000,
        "avg_order_value": 4500,
        "characteristics": ["Discovery phase", "Need guidance", "Promotion responsive"]
    },
    {
        "_id": "seg-dormant",
        "segment_id": "seg-dormant",
        "name": "Dormant Customers",
        "description": "No purchase in 6+ months",
        "criteria": {"last_purchase_days_ago_min": 180},
        "customer_count": 95,
        "total_revenue": 850000,
        "avg_order_value": 5200,
        "characteristics": ["Reactivation potential", "Contact lens users", "Birthday campaigns"]
    },
    {
        "_id": "seg-b2b",
        "segment_id": "seg-b2b",
        "name": "B2B Customers",
        "description": "Corporate and hospital accounts",
        "criteria": {"customer_type": "B2B"},
        "customer_count": 12,
        "total_revenue": 2450000,
        "avg_order_value": 25000,
        "characteristics": ["Bulk orders", "Credit terms", "Regular reorders"]
    }
]

# ============================================================================
# ALERTS & NOTIFICATIONS (for JARVIS)
# ============================================================================
ALERTS = [
    {
        "_id": "alert-001",
        "alert_type": "LOW_STOCK",
        "severity": "WARNING",
        "title": "Low Stock Alert",
        "message": "Ray-Ban Aviator Classic has only 5 units remaining",
        "entity_type": "PRODUCT",
        "entity_id": "prod-fr-001",
        "store_id": "store-001",
        "is_read": False,
        "is_resolved": False,
        "created_at": (now - timedelta(hours=2)).isoformat()
    },
    {
        "_id": "alert-002",
        "alert_type": "PAYMENT_DUE",
        "severity": "HIGH",
        "title": "Payment Collection Due",
        "message": "Order BV-CP-2024-001542 has Rs. 7,431 balance pending for 2 days",
        "entity_type": "ORDER",
        "entity_id": "ord-001",
        "store_id": "store-001",
        "is_read": False,
        "is_resolved": False,
        "created_at": (now - timedelta(hours=6)).isoformat()
    },
    {
        "_id": "alert-003",
        "alert_type": "DELIVERY_DUE",
        "severity": "NORMAL",
        "title": "Delivery Ready",
        "message": "Order BV-CP-2024-001543 is ready for customer pickup",
        "entity_type": "ORDER",
        "entity_id": "ord-002",
        "store_id": "store-001",
        "is_read": True,
        "is_resolved": False,
        "created_at": (now - timedelta(hours=4)).isoformat()
    },
    {
        "_id": "alert-004",
        "alert_type": "TARGET_ACHIEVEMENT",
        "severity": "INFO",
        "title": "Sales Target Update",
        "message": "Store has achieved 85.8% of monthly target with 5 days remaining",
        "entity_type": "STORE",
        "entity_id": "store-001",
        "store_id": "store-001",
        "is_read": False,
        "is_resolved": False,
        "created_at": (now - timedelta(hours=1)).isoformat()
    }
]

# ============================================================================
# SEED FUNCTION
# ============================================================================
def get_all_seed_data():
    """Return all seed data organized by collection"""
    return {
        "stores": STORES,
        "users": USERS,
        "customers": CUSTOMERS,
        "products": PRODUCTS,
        "stock_units": STOCK_UNITS,
        "orders": ORDERS,
        "workshop_jobs": WORKSHOP_JOBS,
        "prescriptions": PRESCRIPTIONS,
        "tasks": TASKS,
        "queue": QUEUE_ITEMS,
        "daily_sales": DAILY_SALES,
        "attendance": ATTENDANCE,
        "business_metrics": BUSINESS_METRICS,
        "customer_segments": CUSTOMER_SEGMENTS,
        "alerts": ALERTS
    }


def seed_collection(collection, data, clear_first=True):
    """Seed a collection with data"""
    if clear_first:
        collection.delete_many({})

    if data:
        collection.insert_many(data)

    return len(data)


def seed_database(db):
    """Seed all collections in the database"""
    seed_data = get_all_seed_data()
    results = {}

    for collection_name, data in seed_data.items():
        collection = db[collection_name]
        count = seed_collection(collection, data)
        results[collection_name] = count
        print(f"  ✓ {collection_name}: {count} documents")

    return results
