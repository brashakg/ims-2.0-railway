-- ============================================
-- IMS 2.0 - DATABASE SCHEMA
-- Better Vision & WizOpt Optical Retail System
-- YOUR EXACT BUSINESS RULES ENCODED
-- ============================================

-- Companies/Brands
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    legal_name VARCHAR(200) NOT NULL,
    gst_number VARCHAR(15),
    pan_number VARCHAR(10),
    state_code VARCHAR(2),
    primary_color VARCHAR(7),  -- '#cd201A' for Better Vision
    secondary_color VARCHAR(7),
    logo_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- Stores with geo-fence for login
CREATE TABLE stores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    code VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    address_line1 VARCHAR(200),
    city VARCHAR(100),
    state VARCHAR(100),
    pincode VARCHAR(6),
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    geo_fence_radius_meters INT DEFAULT 100,
    phone VARCHAR(15),
    opening_time TIME,
    closing_time TIME,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- Roles with YOUR exact discount caps
CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(30) UNIQUE NOT NULL,
    name VARCHAR(50) NOT NULL,
    hierarchy_level INT NOT NULL,
    max_discount_percent DECIMAL(5,2) DEFAULT 0,
    can_access_ai BOOLEAN DEFAULT FALSE,
    can_change_prices BOOLEAN DEFAULT FALSE,
    can_view_all_stores BOOLEAN DEFAULT FALSE,
    can_transfer_stock BOOLEAN DEFAULT FALSE,
    can_approve_discounts BOOLEAN DEFAULT FALSE,
    can_use_cash_drawer BOOLEAN DEFAULT FALSE
);

-- YOUR EXACT DISCOUNT CAPS
INSERT INTO roles (code, name, hierarchy_level, max_discount_percent, can_access_ai, can_change_prices, can_view_all_stores, can_approve_discounts, can_use_cash_drawer) VALUES
('SUPERADMIN', 'Superadmin (CEO)', 1, 100.00, TRUE, TRUE, TRUE, TRUE, TRUE),
('ADMIN', 'Admin (Director)', 2, 100.00, FALSE, TRUE, TRUE, TRUE, TRUE),
('AREA_MANAGER', 'Area Manager', 3, 25.00, FALSE, FALSE, TRUE, TRUE, TRUE),
('STORE_MANAGER', 'Store Manager', 4, 20.00, FALSE, FALSE, FALSE, TRUE, TRUE),
('ACCOUNTANT', 'Accountant', 5, 0.00, FALSE, TRUE, TRUE, FALSE, FALSE),
('CATALOG_MANAGER', 'Catalog Manager', 5, 0.00, FALSE, TRUE, TRUE, FALSE, FALSE),
('OPTOMETRIST', 'Optometrist', 6, 0.00, FALSE, FALSE, FALSE, FALSE, FALSE),
('SALES_CASHIER', 'Sales Staff (Cashier)', 7, 10.00, FALSE, FALSE, FALSE, FALSE, TRUE),
('SALES_STAFF', 'Sales Staff', 8, 10.00, FALSE, FALSE, FALSE, FALSE, FALSE),
('WORKSHOP_OPTICAL', 'Workshop (Optical)', 9, 0.00, FALSE, FALSE, FALSE, FALSE, FALSE),
('WORKSHOP_WATCH', 'Workshop (Watch)', 9, 0.00, FALSE, FALSE, FALSE, FALSE, FALSE);

-- Users
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_code VARCHAR(20) UNIQUE,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50),
    email VARCHAR(100) UNIQUE,
    phone VARCHAR(15) NOT NULL,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    date_of_joining DATE,
    is_active BOOLEAN DEFAULT TRUE,
    last_login_at TIMESTAMP,
    last_login_latitude DECIMAL(10, 8),
    last_login_longitude DECIMAL(11, 8)
);

-- MULTI-ROLE: One user can have multiple roles (e.g., Neha = Store Manager + Optometrist + Sales)
CREATE TABLE user_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    role_id UUID REFERENCES roles(id),
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(user_id, role_id)
);

-- User-Store assignments
CREATE TABLE user_stores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    store_id UUID REFERENCES stores(id),
    is_primary_store BOOLEAN DEFAULT FALSE,
    UNIQUE(user_id, store_id)
);

-- Product Categories with discount classifications
CREATE TABLE product_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(50) NOT NULL,
    discount_class VARCHAR(20) NOT NULL,  -- MASS, PREMIUM, LUXURY, NON_DISCOUNTABLE
    max_discount_override DECIMAL(5,2),  -- Category can cap discount lower than role
    requires_barcode BOOLEAN DEFAULT TRUE,
    requires_expiry_tracking BOOLEAN DEFAULT FALSE
);

INSERT INTO product_categories (code, name, discount_class, requires_barcode, requires_expiry_tracking) VALUES
('FRAME', 'Frame/Sunglass', 'PREMIUM', TRUE, FALSE),
('OPTICAL_LENS', 'Optical Lens', 'PREMIUM', FALSE, FALSE),
('CONTACT_LENS', 'Contact Lens', 'PREMIUM', FALSE, TRUE),
('WATCH', 'Watch', 'PREMIUM', TRUE, FALSE),
('SMARTWATCH', 'Smartwatch', 'MASS', TRUE, FALSE),
('SMART_GLASSES', 'Smart Glasses', 'LUXURY', TRUE, FALSE),
('ACCESSORIES', 'Accessories', 'MASS', FALSE, FALSE),
('SERVICE', 'Services', 'MASS', FALSE, FALSE);

-- Brands
CREATE TABLE brands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    brand_group VARCHAR(50)  -- LUXURY, PREMIUM, BUDGET
);

-- Products with YOUR MRP/Offer Price rule
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id UUID REFERENCES product_categories(id),
    brand_id UUID REFERENCES brands(id),
    sku VARCHAR(50) UNIQUE NOT NULL,
    model_number VARCHAR(50),
    barcode VARCHAR(50),
    name VARCHAR(200) NOT NULL,
    color_code VARCHAR(20),
    color_name VARCHAR(50),
    size VARCHAR(10),
    mrp DECIMAL(10,2) NOT NULL,
    offer_price DECIMAL(10,2) NOT NULL,
    cost_price DECIMAL(10,2),
    gst_percent DECIMAL(5,2) DEFAULT 18.00,
    primary_image_url TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    -- YOUR CRITICAL RULE: Offer price can NEVER exceed MRP
    CONSTRAINT chk_offer_not_exceeds_mrp CHECK (offer_price <= mrp)
);

-- Store Stock
CREATE TABLE store_stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id UUID REFERENCES stores(id),
    product_id UUID REFERENCES products(id),
    quantity INT DEFAULT 0,
    location_code VARCHAR(20),  -- C1, D2 etc.
    batch_code VARCHAR(50),
    expiry_date DATE,
    UNIQUE(store_id, product_id, COALESCE(batch_code, ''))
);

-- Customers
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_type VARCHAR(10) NOT NULL,  -- B2C, B2B
    name VARCHAR(100),
    legal_name VARCHAR(200),
    gst_number VARCHAR(15),
    phone VARCHAR(15) NOT NULL,
    email VARCHAR(100),
    city VARCHAR(100),
    state VARCHAR(100),
    credit_enabled BOOLEAN DEFAULT FALSE,
    primary_store_id UUID REFERENCES stores(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Patients (family members under customer)
CREATE TABLE patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id),
    name VARCHAR(100) NOT NULL,
    phone VARCHAR(15),
    date_of_birth DATE,
    engraving_text VARCHAR(50)
);

-- Prescriptions
CREATE TABLE prescriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prescription_number VARCHAR(20) UNIQUE NOT NULL,
    patient_id UUID REFERENCES patients(id),
    source VARCHAR(20) NOT NULL,  -- STORE_TEST, EXTERNAL_DOCTOR
    optometrist_id UUID REFERENCES users(id),
    r_sph DECIMAL(5,2), r_cyl DECIMAL(5,2), r_axis INT, r_add DECIMAL(5,2),
    l_sph DECIMAL(5,2), l_cyl DECIMAL(5,2), l_axis INT, l_add DECIMAL(5,2),
    valid_until DATE,
    store_id UUID REFERENCES stores(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_r_axis CHECK (r_axis IS NULL OR (r_axis >= 1 AND r_axis <= 180)),
    CONSTRAINT chk_l_axis CHECK (l_axis IS NULL OR (l_axis >= 1 AND l_axis <= 180))
);

-- Orders
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(20) UNIQUE NOT NULL,
    store_id UUID REFERENCES stores(id),
    customer_id UUID REFERENCES customers(id),
    patient_id UUID REFERENCES patients(id),
    salesperson_id UUID REFERENCES users(id),
    subtotal DECIMAL(12,2) NOT NULL,
    total_discount DECIMAL(12,2) DEFAULT 0,
    total_gst DECIMAL(12,2) DEFAULT 0,
    grand_total DECIMAL(12,2) NOT NULL,
    amount_paid DECIMAL(12,2) DEFAULT 0,
    amount_due DECIMAL(12,2),
    payment_status VARCHAR(20) DEFAULT 'PENDING',
    status VARCHAR(20) DEFAULT 'DRAFT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Order Items with discount tracking
CREATE TABLE order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id),
    product_id UUID REFERENCES products(id),
    prescription_id UUID REFERENCES prescriptions(id),
    quantity INT DEFAULT 1,
    mrp DECIMAL(10,2) NOT NULL,
    offer_price DECIMAL(10,2) NOT NULL,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    discount_amount DECIMAL(10,2) DEFAULT 0,
    discount_approved_by UUID REFERENCES users(id),
    unit_price DECIMAL(10,2) NOT NULL,
    gst_amount DECIMAL(10,2),
    line_total DECIMAL(12,2) NOT NULL
);

-- Payments
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id),
    amount DECIMAL(12,2) NOT NULL,
    payment_method VARCHAR(20) NOT NULL,
    transaction_reference VARCHAR(100),
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Discount Audit (YOUR requirement for tracking)
CREATE TABLE discount_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_item_id UUID REFERENCES order_items(id),
    attempted_by UUID REFERENCES users(id),
    requested_discount_percent DECIMAL(5,2),
    user_max_discount DECIMAL(5,2),
    category_max_discount DECIMAL(5,2),
    mrp DECIMAL(10,2),
    offer_price DECIMAL(10,2),
    discount_blocked_by_pricing BOOLEAN,  -- TRUE if offer_price < MRP
    status VARCHAR(20) NOT NULL,
    approved_by UUID REFERENCES users(id),
    ai_flagged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Activity Logs (for Superadmin visibility)
CREATE TABLE activity_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    store_id UUID REFERENCES stores(id),
    action VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50),
    entity_id UUID,
    old_values JSONB,
    new_values JSONB,
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tasks with non-customizable priority colors
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    assigned_to UUID REFERENCES users(id),
    assigned_by UUID REFERENCES users(id),
    store_id UUID REFERENCES stores(id),
    priority VARCHAR(10) NOT NULL,  -- P0=DarkRed, P1=Red, P2=Orange, P3=Yellow, P4=Blue
    due_date TIMESTAMP,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_store_stock ON store_stock(store_id, product_id);
CREATE INDEX idx_orders_store ON orders(store_id);
CREATE INDEX idx_activity_logs ON activity_logs(user_id, created_at);
