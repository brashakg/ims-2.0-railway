// ============================================================================
// IMS 2.0 - MongoDB Initialization Script
// ============================================================================
// Creates database, collections, and indexes on first run
// ============================================================================

// Switch to IMS database
db = db.getSiblingDB('ims_2_0');

print('🚀 Initializing IMS 2.0 Database...');
print('');

// ============================================================================
// CREATE COLLECTIONS
// ============================================================================

const collections = [
    'users',
    'stores',
    'products',
    'stock_units',
    'customers',
    'prescriptions',
    'orders',
    'vendors',
    'purchase_orders',
    'grns',
    'tasks',
    'expenses',
    'advances',
    'attendance',
    'leaves',
    'payroll',
    'workshop_jobs',
    'audit_logs',
    'notifications'
];

print('📦 Creating collections...');
collections.forEach(collectionName => {
    try {
        db.createCollection(collectionName);
        print(`  ✓ ${collectionName}`);
    } catch (e) {
        print(`  ⚠ ${collectionName} already exists`);
    }
});
print('');

// ============================================================================
// CREATE INDEXES
// ============================================================================

print('📇 Creating indexes...');

// Users collection indexes
db.users.createIndex({ username: 1 }, { unique: true });
db.users.createIndex({ email: 1 }, { unique: true, sparse: true });
db.users.createIndex({ roles: 1 });
db.users.createIndex({ store_ids: 1 });
db.users.createIndex({ is_active: 1 });
print('  ✓ users indexes');

// Stores collection indexes
db.stores.createIndex({ store_code: 1 }, { unique: true });
db.stores.createIndex({ is_active: 1 });
db.stores.createIndex({ brand: 1 });
print('  ✓ stores indexes');

// Products collection indexes
db.products.createIndex({ sku: 1 }, { unique: true });
db.products.createIndex({ category: 1 });
db.products.createIndex({ sub_category: 1 });
db.products.createIndex({ brand: 1 });
db.products.createIndex({ is_active: 1 });
db.products.createIndex({ 'pricing.mrp': 1 });
db.products.createIndex({ created_at: -1 });
print('  ✓ products indexes');

// Stock units collection indexes
db.stock_units.createIndex({ product_id: 1, store_id: 1 });
db.stock_units.createIndex({ store_id: 1, status: 1 });
db.stock_units.createIndex({ serial_number: 1 }, { unique: true, sparse: true });
db.stock_units.createIndex({ status: 1 });
db.stock_units.createIndex({ created_at: -1 });
print('  ✓ stock_units indexes');

// Customers collection indexes
db.customers.createIndex({ phone: 1 }, { unique: true });
db.customers.createIndex({ email: 1 }, { unique: true, sparse: true });
db.customers.createIndex({ customer_code: 1 }, { unique: true });
db.customers.createIndex({ is_patient: 1 });
db.customers.createIndex({ created_at: -1 });
print('  ✓ customers indexes');

// Prescriptions collection indexes
db.prescriptions.createIndex({ customer_id: 1 });
db.prescriptions.createIndex({ prescription_number: 1 }, { unique: true });
db.prescriptions.createIndex({ store_id: 1 });
db.prescriptions.createIndex({ created_at: -1 });
print('  ✓ prescriptions indexes');

// Orders collection indexes
db.orders.createIndex({ order_number: 1 }, { unique: true });
db.orders.createIndex({ customer_id: 1 });
db.orders.createIndex({ store_id: 1 });
db.orders.createIndex({ status: 1 });
db.orders.createIndex({ order_date: -1 });
db.orders.createIndex({ created_at: -1 });
print('  ✓ orders indexes');

// Vendors collection indexes
db.vendors.createIndex({ vendor_code: 1 }, { unique: true });
db.vendors.createIndex({ is_active: 1 });
db.vendors.createIndex({ created_at: -1 });
print('  ✓ vendors indexes');

// Purchase orders collection indexes
db.purchase_orders.createIndex({ po_number: 1 }, { unique: true });
db.purchase_orders.createIndex({ vendor_id: 1 });
db.purchase_orders.createIndex({ store_id: 1 });
db.purchase_orders.createIndex({ status: 1 });
db.purchase_orders.createIndex({ created_at: -1 });
print('  ✓ purchase_orders indexes');

// GRNs collection indexes
db.grns.createIndex({ grn_number: 1 }, { unique: true });
db.grns.createIndex({ po_id: 1 });
db.grns.createIndex({ store_id: 1 });
db.grns.createIndex({ created_at: -1 });
print('  ✓ grns indexes');

// Tasks collection indexes
db.tasks.createIndex({ store_id: 1, status: 1 });
db.tasks.createIndex({ assigned_to: 1, status: 1 });
db.tasks.createIndex({ due_date: 1 });
db.tasks.createIndex({ priority: 1 });
db.tasks.createIndex({ created_at: -1 });
print('  ✓ tasks indexes');

// Expenses collection indexes
db.expenses.createIndex({ store_id: 1, expense_date: -1 });
db.expenses.createIndex({ category: 1 });
db.expenses.createIndex({ approved_by: 1 });
db.expenses.createIndex({ created_at: -1 });
print('  ✓ expenses indexes');

// Advances collection indexes
db.advances.createIndex({ user_id: 1 });
db.advances.createIndex({ store_id: 1 });
db.advances.createIndex({ status: 1 });
db.advances.createIndex({ created_at: -1 });
print('  ✓ advances indexes');

// Attendance collection indexes
db.attendance.createIndex({ user_id: 1, date: 1 }, { unique: true });
db.attendance.createIndex({ store_id: 1, date: -1 });
db.attendance.createIndex({ created_at: -1 });
print('  ✓ attendance indexes');

// Leaves collection indexes
db.leaves.createIndex({ user_id: 1, start_date: -1 });
db.leaves.createIndex({ store_id: 1, status: 1 });
db.leaves.createIndex({ status: 1 });
db.leaves.createIndex({ created_at: -1 });
print('  ✓ leaves indexes');

// Payroll collection indexes
db.payroll.createIndex({ user_id: 1, month: 1, year: 1 }, { unique: true });
db.payroll.createIndex({ store_id: 1 });
db.payroll.createIndex({ created_at: -1 });
print('  ✓ payroll indexes');

// Workshop jobs collection indexes
db.workshop_jobs.createIndex({ job_number: 1 }, { unique: true });
db.workshop_jobs.createIndex({ order_id: 1 });
db.workshop_jobs.createIndex({ store_id: 1 });
db.workshop_jobs.createIndex({ status: 1 });
db.workshop_jobs.createIndex({ created_at: -1 });
print('  ✓ workshop_jobs indexes');

// Audit logs collection indexes
db.audit_logs.createIndex({ entity_type: 1, entity_id: 1 });
db.audit_logs.createIndex({ user_id: 1 });
db.audit_logs.createIndex({ timestamp: -1 });
db.audit_logs.createIndex({ action: 1 });
print('  ✓ audit_logs indexes');

// Notifications collection indexes
db.notifications.createIndex({ user_id: 1, read: 1 });
db.notifications.createIndex({ created_at: -1 });
print('  ✓ notifications indexes');

print('');

// ============================================================================
// CREATE DEFAULT ADMIN USER
// ============================================================================

print('👤 Creating default admin user...');

const defaultAdminExists = db.users.findOne({ username: 'admin' });

if (!defaultAdminExists) {
    db.users.insertOne({
        username: 'admin',
        email: 'admin@ims2.com',
        // Password: admin123 (hashed with bcrypt)
        // IMPORTANT: Change this password after first login!
        password_hash: '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYWJQrKWxjS',
        full_name: 'System Administrator',
        roles: ['SUPERADMIN'],
        store_ids: [],
        is_active: true,
        created_at: new Date(),
        updated_at: new Date()
    });
    print('  ✓ Admin user created');
    print('    Username: admin');
    print('    Password: admin123');
    print('    ⚠️  CHANGE PASSWORD AFTER FIRST LOGIN!');
} else {
    print('  ⚠ Admin user already exists');
}

print('');

// ============================================================================
// INITIALIZATION COMPLETE
// ============================================================================

print('============================================================================');
print('✅ IMS 2.0 Database Initialization Complete!');
print('============================================================================');
print('');
print('Default credentials:');
print('  Username: admin');
print('  Password: admin123');
print('');
print('⚠️  IMPORTANT: Change the default password immediately!');
print('');
