"""
IMS 2.0 - Better Vision Opticals - Production Seed Data
=========================================================
Correct store data for: 2 Bokaro, 3 Dhanbad (incl WizOpt), 1 Pune
Per SYSTEM_INTENT.md - this is authoritative seed data.
"""
from datetime import datetime, timedelta
import uuid


def _pw():
    """Bcrypt hash for 'admin123' - all seed accounts use this"""
    return "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.rP1zLjHZs8HPXW"


NOW = datetime.utcnow().isoformat()

# ============================================================================
# STORES - Better Vision Opticals Pvt Ltd
# ============================================================================
STORES = [
    {
        "_id": "BV-BOK-01", "store_id": "BV-BOK-01", "store_code": "BV-BOK-01",
        "store_name": "Better Vision - Bokaro Steel City",
        "brand": "BETTER_VISION",
        "address": "City Centre, Sector 4", "city": "Bokaro Steel City",
        "state": "Jharkhand", "pincode": "827004",
        "phone": "+91 6542 000001", "email": "bokaro1@bettervision.in",
        "gstin": "20AABCB0001Q1ZP", "is_active": True,
        "enabled_categories": ["FRAMES", "SUNGLASSES", "RX_LENSES", "CONTACT_LENSES",
            "COLOUR_CONTACTS", "SMARTGLASSES", "WRIST_WATCHES", "SMARTWATCHES",
            "WALL_CLOCKS", "ACCESSORIES"],
        "created_at": NOW,
    },
    {
        "_id": "BV-BOK-02", "store_id": "BV-BOK-02", "store_code": "BV-BOK-02",
        "store_name": "Better Vision - Bokaro Chas",
        "brand": "BETTER_VISION",
        "address": "Main Road, Chas", "city": "Bokaro Steel City",
        "state": "Jharkhand", "pincode": "827013",
        "phone": "+91 6542 000002", "email": "bokaro2@bettervision.in",
        "gstin": "20AABCB0002Q1ZP", "is_active": True,
        "enabled_categories": ["FRAMES", "SUNGLASSES", "RX_LENSES", "CONTACT_LENSES",
            "COLOUR_CONTACTS", "WRIST_WATCHES", "ACCESSORIES"],
        "created_at": NOW,
    },
    {
        "_id": "BV-DHN-01", "store_id": "BV-DHN-01", "store_code": "BV-DHN-01",
        "store_name": "Better Vision - Dhanbad Central",
        "brand": "BETTER_VISION",
        "address": "Hirapur Main Road", "city": "Dhanbad",
        "state": "Jharkhand", "pincode": "826001",
        "phone": "+91 326 000001", "email": "dhanbad1@bettervision.in",
        "gstin": "20AABCB0003Q1ZP", "is_active": True,
        "enabled_categories": ["FRAMES", "SUNGLASSES", "RX_LENSES", "CONTACT_LENSES",
            "COLOUR_CONTACTS", "SMARTGLASSES", "WRIST_WATCHES", "SMARTWATCHES",
            "WALL_CLOCKS", "ACCESSORIES"],
        "created_at": NOW,
    },
    {
        "_id": "BV-DHN-02", "store_id": "BV-DHN-02", "store_code": "BV-DHN-02",
        "store_name": "Better Vision - Dhanbad Govindpur",
        "brand": "BETTER_VISION",
        "address": "Govindpur Road", "city": "Dhanbad",
        "state": "Jharkhand", "pincode": "828109",
        "phone": "+91 326 000002", "email": "dhanbad2@bettervision.in",
        "gstin": "20AABCB0004Q1ZP", "is_active": True,
        "enabled_categories": ["FRAMES", "SUNGLASSES", "RX_LENSES", "CONTACT_LENSES",
            "WRIST_WATCHES", "ACCESSORIES"],
        "created_at": NOW,
    },
    {
        "_id": "WO-DHN-01", "store_id": "WO-DHN-01", "store_code": "WO-DHN-01",
        "store_name": "WizOpt - Dhanbad",
        "brand": "WIZOPT",
        "address": "Saraidhela Market", "city": "Dhanbad",
        "state": "Jharkhand", "pincode": "828127",
        "phone": "+91 326 000003", "email": "wizopt@bettervision.in",
        "gstin": "20AABCB0005Q1ZP", "is_active": True,
        "enabled_categories": ["FRAMES", "SUNGLASSES", "RX_LENSES", "CONTACT_LENSES",
            "COLOUR_CONTACTS", "SMARTGLASSES", "WRIST_WATCHES", "SMARTWATCHES",
            "ACCESSORIES"],
        "created_at": NOW,
    },
    {
        "_id": "BV-PUN-01", "store_id": "BV-PUN-01", "store_code": "BV-PUN-01",
        "store_name": "Better Vision - Pune",
        "brand": "BETTER_VISION",
        "address": "FC Road, Shivajinagar", "city": "Pune",
        "state": "Maharashtra", "pincode": "411005",
        "phone": "+91 20 000001", "email": "pune@bettervision.in",
        "gstin": "27AABCB0006Q1ZP", "is_active": True,
        "enabled_categories": ["FRAMES", "SUNGLASSES", "RX_LENSES", "CONTACT_LENSES",
            "COLOUR_CONTACTS", "SMARTGLASSES", "WRIST_WATCHES", "SMARTWATCHES",
            "WALL_CLOCKS", "ACCESSORIES"],
        "created_at": NOW,
    },
]

ALL_STORE_IDS = [s["store_id"] for s in STORES]

# ============================================================================
# USERS - Role hierarchy per SYSTEM_INTENT.md (11 roles)
# ============================================================================
USERS = [
    {"_id": "user-superadmin", "user_id": "user-superadmin", "username": "admin",
     "password_hash": _pw(), "full_name": "Avinash (Superadmin)",
     "email": "admin@bettervision.in", "phone": "9999999999",
     "roles": ["SUPERADMIN"], "store_ids": ALL_STORE_IDS,
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-admin-hq", "user_id": "user-admin-hq", "username": "hq_admin",
     "password_hash": _pw(), "full_name": "HQ Admin",
     "email": "hq@bettervision.in", "phone": "9999999998",
     "roles": ["ADMIN"], "store_ids": ALL_STORE_IDS,
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-areamgr-jh", "user_id": "user-areamgr-jh", "username": "area_jharkhand",
     "password_hash": _pw(), "full_name": "Area Manager Jharkhand",
     "email": "area.jh@bettervision.in", "phone": "9100000000",
     "roles": ["AREA_MANAGER"], "store_ids": ["BV-BOK-01","BV-BOK-02","BV-DHN-01","BV-DHN-02","WO-DHN-01"],
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-mgr-bok1", "user_id": "user-mgr-bok1", "username": "mgr_bokaro1",
     "password_hash": _pw(), "full_name": "Store Manager Bokaro 1",
     "email": "mgr.bokaro1@bettervision.in", "phone": "9100000001",
     "roles": ["STORE_MANAGER"], "store_ids": ["BV-BOK-01"],
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-opto-bok1", "user_id": "user-opto-bok1", "username": "opto_bokaro1",
     "password_hash": _pw(), "full_name": "Dr. Optometrist Bokaro",
     "email": "opto.bokaro1@bettervision.in", "phone": "9100000002",
     "roles": ["OPTOMETRIST"], "store_ids": ["BV-BOK-01","BV-BOK-02"],
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-sales-bok1", "user_id": "user-sales-bok1", "username": "sales_bokaro1",
     "password_hash": _pw(), "full_name": "Sales Staff Bokaro 1",
     "email": "sales.bokaro1@bettervision.in", "phone": "9100000003",
     "roles": ["SALES_STAFF"], "store_ids": ["BV-BOK-01"],
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-cashier-bok1", "user_id": "user-cashier-bok1", "username": "cashier_bokaro1",
     "password_hash": _pw(), "full_name": "Cashier Bokaro 1",
     "email": "cashier.bokaro1@bettervision.in", "phone": "9100000004",
     "roles": ["CASHIER"], "store_ids": ["BV-BOK-01"],
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-workshop-bok1", "user_id": "user-workshop-bok1", "username": "workshop_bokaro1",
     "password_hash": _pw(), "full_name": "Workshop Staff Bokaro",
     "email": "workshop.bokaro1@bettervision.in", "phone": "9100000005",
     "roles": ["WORKSHOP_STAFF"], "store_ids": ["BV-BOK-01"],
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-catalog-hq", "user_id": "user-catalog-hq", "username": "catalog_hq",
     "password_hash": _pw(), "full_name": "Catalog Manager HQ",
     "email": "catalog@bettervision.in", "phone": "9100000006",
     "roles": ["CATALOG_MANAGER"], "store_ids": ALL_STORE_IDS,
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-accountant", "user_id": "user-accountant", "username": "accountant",
     "password_hash": _pw(), "full_name": "Accountant HQ",
     "email": "accounts@bettervision.in", "phone": "9100000010",
     "roles": ["ACCOUNTANT"], "store_ids": ALL_STORE_IDS,
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-invhq", "user_id": "user-invhq", "username": "inventory_hq",
     "password_hash": _pw(), "full_name": "Inventory HQ Team",
     "email": "inventory@bettervision.in", "phone": "9100000011",
     "roles": ["INVENTORY_HQ"], "store_ids": ALL_STORE_IDS,
     "active_store_id": "BV-BOK-01", "is_active": True, "created_at": NOW},
    {"_id": "user-mgr-dhn1", "user_id": "user-mgr-dhn1", "username": "mgr_dhanbad1",
     "password_hash": _pw(), "full_name": "Store Manager Dhanbad Central",
     "email": "mgr.dhanbad1@bettervision.in", "phone": "9200000001",
     "roles": ["STORE_MANAGER"], "store_ids": ["BV-DHN-01"],
     "active_store_id": "BV-DHN-01", "is_active": True, "created_at": NOW},
    {"_id": "user-mgr-pune", "user_id": "user-mgr-pune", "username": "mgr_pune",
     "password_hash": _pw(), "full_name": "Store Manager Pune",
     "email": "mgr.pune@bettervision.in", "phone": "9300000001",
     "roles": ["STORE_MANAGER"], "store_ids": ["BV-PUN-01"],
     "active_store_id": "BV-PUN-01", "is_active": True, "created_at": NOW},
]

# ============================================================================
# PRODUCTS - Per CATEGORY_ATTRIBUTE_MODEL.md (6 core + extras)
# ============================================================================
PRODUCTS = [
    # --- FRAMES ---
    {"_id": "prod-fr-001", "product_id": "prod-fr-001", "name": "Ray-Ban RB5154 Clubmaster",
     "sku": "FR-RAYB-5154-BLK", "category": "FRAMES", "brand": "Ray-Ban",
     "model": "RB5154", "mrp": 8490, "offer_price": 8490,
     "hsn_code": "9004", "gst_rate": 18, "frame_type": "Full Rim",
     "frame_shape": "Clubmaster", "frame_material": "Acetate + Metal",
     "frame_color": "Black Gold", "frame_size": "51-21-145", "gender": "Unisex",
     "is_active": True, "created_at": NOW},
    {"_id": "prod-fr-002", "product_id": "prod-fr-002", "name": "Titan Eyeplus T2001",
     "sku": "FR-TITN-T2001-BRN", "category": "FRAMES", "brand": "Titan Eyeplus",
     "model": "T2001", "mrp": 2990, "offer_price": 2490,
     "hsn_code": "9004", "gst_rate": 18, "frame_type": "Full Rim",
     "frame_shape": "Rectangle", "frame_material": "TR90",
     "frame_color": "Brown", "frame_size": "52-18-140", "gender": "Male",
     "is_active": True, "created_at": NOW},
    {"_id": "prod-fr-003", "product_id": "prod-fr-003", "name": "Lenskart Air Flex LA-5012",
     "sku": "FR-LKAF-5012-BLU", "category": "FRAMES", "brand": "Lenskart Air",
     "model": "LA-5012", "mrp": 1499, "offer_price": 999,
     "hsn_code": "9004", "gst_rate": 18, "frame_type": "Half Rim",
     "frame_shape": "Round", "frame_material": "Metal",
     "frame_color": "Blue", "frame_size": "49-20-140", "gender": "Female",
     "is_active": True, "created_at": NOW},

    # --- SUNGLASSES ---
    {"_id": "prod-sg-001", "product_id": "prod-sg-001", "name": "Ray-Ban Aviator RB3025",
     "sku": "SG-RAYB-3025-GLD", "category": "SUNGLASSES", "brand": "Ray-Ban",
     "model": "RB3025", "mrp": 12990, "offer_price": 12990,
     "hsn_code": "9004", "gst_rate": 18, "lens_type": "Polarized",
     "lens_color": "Green Classic G-15", "uv_protection": "UV400", "gender": "Unisex",
     "is_active": True, "created_at": NOW},
    {"_id": "prod-sg-002", "product_id": "prod-sg-002", "name": "Fastrack P357BK1",
     "sku": "SG-FAST-P357-BLK", "category": "SUNGLASSES", "brand": "Fastrack",
     "model": "P357BK1", "mrp": 1490, "offer_price": 1190,
     "hsn_code": "9004", "gst_rate": 18, "lens_type": "Non-Polarized",
     "gender": "Male", "is_active": True, "created_at": NOW},

    # --- RX LENSES ---
    {"_id": "prod-rx-001", "product_id": "prod-rx-001", "name": "Essilor Crizal Alize 1.67",
     "sku": "RX-ESSL-CRZL-167", "category": "RX_LENSES", "brand": "Essilor",
     "model": "Crizal Alize", "mrp": 8500, "offer_price": 7200,
     "hsn_code": "9001", "gst_rate": 12, "lens_index": "1.67",
     "lens_material": "Polycarbonate", "coating": "Anti-Reflective + Blue Cut",
     "lens_design": "Single Vision", "is_active": True, "created_at": NOW},
    {"_id": "prod-rx-002", "product_id": "prod-rx-002", "name": "Zeiss DriveSafe Progressive",
     "sku": "RX-ZEIS-DRSF-PRG", "category": "RX_LENSES", "brand": "Zeiss",
     "model": "DriveSafe", "mrp": 18000, "offer_price": 15500,
     "hsn_code": "9001", "gst_rate": 12, "lens_index": "1.6",
     "lens_design": "Progressive", "is_active": True, "created_at": NOW},

    # --- CONTACT LENSES ---
    {"_id": "prod-cl-001", "product_id": "prod-cl-001", "name": "Bausch+Lomb SofLens Monthly",
     "sku": "CL-BAUL-SFLM-030", "category": "CONTACT_LENSES", "brand": "Bausch+Lomb",
     "model": "SofLens 59", "mrp": 650, "offer_price": 550,
     "hsn_code": "9001", "gst_rate": 12, "replacement_schedule": "Monthly",
     "pack_size": 6, "is_active": True, "created_at": NOW},

    # --- COLOUR CONTACTS ---
    {"_id": "prod-cc-001", "product_id": "prod-cc-001", "name": "FreshLook ColorBlends Hazel",
     "sku": "CC-ALCO-FLCB-HZL", "category": "COLOUR_CONTACTS", "brand": "Alcon",
     "model": "FreshLook ColorBlends", "mrp": 1650, "offer_price": 1399,
     "hsn_code": "9001", "gst_rate": 12, "color": "Hazel",
     "replacement_schedule": "Monthly", "pack_size": 2,
     "is_active": True, "created_at": NOW},

    # --- WRIST WATCHES ---
    {"_id": "prod-ww-001", "product_id": "prod-ww-001", "name": "Titan Karishma 1578YM05",
     "sku": "WW-TITN-1578-GLD", "category": "WRIST_WATCHES", "brand": "Titan",
     "model": "1578YM05", "mrp": 3495, "offer_price": 2995,
     "hsn_code": "9102", "gst_rate": 18, "watch_type": "Analog",
     "strap_material": "Leather", "gender": "Male",
     "is_active": True, "created_at": NOW},

    # --- SMARTWATCHES ---
    {"_id": "prod-sw-001", "product_id": "prod-sw-001", "name": "Noise ColorFit Pro 4",
     "sku": "SW-NOIS-CFP4-BLK", "category": "SMARTWATCHES", "brand": "Noise",
     "model": "ColorFit Pro 4", "mrp": 4999, "offer_price": 3499,
     "hsn_code": "8517", "gst_rate": 18, "display_type": "AMOLED",
     "is_active": True, "created_at": NOW},

    # --- SMARTGLASSES ---
    {"_id": "prod-gl-001", "product_id": "prod-gl-001", "name": "Titan EyeX Smart Audio Glasses",
     "sku": "GL-TITN-EYEX-BLK", "category": "SMARTGLASSES", "brand": "Titan",
     "model": "EyeX", "mrp": 9999, "offer_price": 8499,
     "hsn_code": "9004", "gst_rate": 18, "connectivity": "Bluetooth 5.3",
     "is_active": True, "created_at": NOW},

    # --- WALL CLOCKS ---
    {"_id": "prod-wc-001", "product_id": "prod-wc-001", "name": "Titan Contemporary Wall Clock",
     "sku": "WC-TITN-CWC-WHT", "category": "WALL_CLOCKS", "brand": "Titan",
     "model": "W0057PA02", "mrp": 1995, "offer_price": 1695,
     "hsn_code": "9105", "gst_rate": 18, "clock_type": "Analog",
     "is_active": True, "created_at": NOW},

    # --- ACCESSORIES ---
    {"_id": "prod-ac-001", "product_id": "prod-ac-001", "name": "Premium Microfiber Cleaning Cloth",
     "sku": "AC-GNRC-MFCL-001", "category": "ACCESSORIES", "brand": "Generic",
     "model": "MF-Clean", "mrp": 99, "offer_price": 79,
     "hsn_code": "6307", "gst_rate": 12, "accessory_type": "Cleaning",
     "is_active": True, "created_at": NOW},
    {"_id": "prod-ac-002", "product_id": "prod-ac-002", "name": "Hard Shell Spectacle Case",
     "sku": "AC-GNRC-HCASE-001", "category": "ACCESSORIES", "brand": "Generic",
     "model": "HC-001", "mrp": 299, "offer_price": 249,
     "hsn_code": "4202", "gst_rate": 18, "accessory_type": "Case",
     "is_active": True, "created_at": NOW},
]

# ============================================================================
# CUSTOMERS
# ============================================================================
CUSTOMERS = [
    {"_id": "cust-001", "customer_id": "cust-001", "name": "Rajesh Kumar",
     "phone": "9876543210", "email": "rajesh.kumar@gmail.com",
     "city": "Bokaro Steel City", "state": "Jharkhand",
     "primary_store_id": "BV-BOK-01", "customer_type": "WALK_IN",
     "created_at": NOW},
    {"_id": "cust-002", "customer_id": "cust-002", "name": "Priya Sharma",
     "phone": "9876543211", "email": "priya.sharma@gmail.com",
     "city": "Dhanbad", "state": "Jharkhand",
     "primary_store_id": "BV-DHN-01", "customer_type": "REGULAR",
     "loyalty_points": 250, "created_at": NOW},
    {"_id": "cust-003", "customer_id": "cust-003", "name": "Amit Singh",
     "phone": "9876543212", "city": "Pune", "state": "Maharashtra",
     "primary_store_id": "BV-PUN-01", "customer_type": "WALK_IN",
     "created_at": NOW},
    {"_id": "cust-004", "customer_id": "cust-004", "name": "Sunita Devi",
     "phone": "9876543213", "city": "Bokaro Steel City", "state": "Jharkhand",
     "primary_store_id": "BV-BOK-02", "customer_type": "REGULAR",
     "loyalty_points": 500, "created_at": NOW},
    {"_id": "cust-005", "customer_id": "cust-005", "name": "ABC Enterprises",
     "phone": "9876543214", "email": "purchase@abcent.com",
     "city": "Dhanbad", "state": "Jharkhand",
     "primary_store_id": "BV-DHN-01", "customer_type": "B2B",
     "gstin": "20AABCA0001Q1ZP", "created_at": NOW},
]


def get_all_seed_data():
    """Return all seed data as a dict of collection_name -> list of documents"""
    return {
        "stores": STORES,
        "users": USERS,
        "products": PRODUCTS,
        "customers": CUSTOMERS,
    }
