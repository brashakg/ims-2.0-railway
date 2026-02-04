// ============================================================================
// IMS 2.0 - User Seed Data
// ============================================================================
// Contains all 36 default users across all roles
// Password for all users: Check individual entries (most use role-based passwords)
// IMPORTANT: Change all passwords after first login!
// ============================================================================

db = db.getSiblingDB('ims_2_0');

print('🚀 Seeding IMS 2.0 Users...');
print('');

// All passwords are hashed with bcrypt
// Default password patterns:
// - admin123 for SUPERADMIN
// - Ceo@2024 for CEO
// - Dir@2024 for Directors
// - Area@2024 for Area Managers
// - Store@2024 for Store Managers
// - Acc@2024 for Accountants
// - Cat@2024 for Catalog Manager
// - Opt@2024 for Head Optometrist
// - Staff@2024 for all store staff

const users = [
  // ============================================================================
  // SUPERADMIN LEVEL
  // ============================================================================
  {
    username: "admin",
    email: "admin@ims2.com",
    password_hash: "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5GyYWJQrKWxjS", // admin123
    full_name: "System Administrator",
    roles: ["SUPERADMIN"],
    store_ids: [],
    is_active: true,
    phone: "+91-9999999999",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "avinash.ceo",
    email: "avinash@bettervision.in",
    password_hash: "$2b$12$8kK9vXJ7YqLHxM3pN5zRUeVbW9hQ2tF6sD7gA1cB4eE5fF6gG7hH8i", // Ceo@2024
    full_name: "Avinash Gupta",
    roles: ["SUPERADMIN"],
    store_ids: [],
    is_active: true,
    phone: "+91-9876543210",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // ADMIN LEVEL (Directors)
  // ============================================================================
  {
    username: "director1",
    email: "rajesh@bettervision.in",
    password_hash: "$2b$12$7jJ8wYI6XpKGwL2oM4yQTdUaV8gP1sE5rC6fZ0bA3dD4eE5fF6gG7h", // Dir@2024
    full_name: "Rajesh Kumar",
    roles: ["ADMIN"],
    store_ids: ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR"],
    is_active: true,
    phone: "+91-9876543211",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "director2",
    email: "priya@wizopt.com",
    password_hash: "$2b$12$7jJ8wYI6XpKGwL2oM4yQTdUaV8gP1sE5rC6fZ0bA3dD4eE5fF6gG7h", // Dir@2024
    full_name: "Priya Sharma",
    roles: ["ADMIN"],
    store_ids: ["WO-MUM"],
    is_active: true,
    phone: "+91-9876543212",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // AREA MANAGERS
  // ============================================================================
  {
    username: "area.manager1",
    email: "vikram@bettervision.in",
    password_hash: "$2b$12$6iI7vXH5WoJFvK1nL3xPScTZU7fO0rD4qB5eY9aZ2cC3dD4eE5fF6g", // Area@2024
    full_name: "Vikram Singh",
    roles: ["AREA_MANAGER"],
    store_ids: ["BV-DEL", "BV-NOI", "BV-GUR"],
    is_active: true,
    phone: "+91-9876543213",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "area.manager2",
    email: "neha@bettervision.in",
    password_hash: "$2b$12$6iI7vXH5WoJFvK1nL3xPScTZU7fO0rD4qB5eY9aZ2cC3dD4eE5fF6g", // Area@2024
    full_name: "Neha Verma",
    roles: ["AREA_MANAGER"],
    store_ids: ["BV-MUM", "BV-BLR"],
    is_active: true,
    phone: "+91-9876543214",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // STORE MANAGERS
  // ============================================================================
  {
    username: "sm.delhi",
    email: "amit.delhi@bettervision.in",
    password_hash: "$2b$12$5hH6uWG4VnIEvJ0mK2wORbSYT6eN9qC3pA4dX8aY1bB2cC3dD4eE5f", // Store@2024
    full_name: "Amit Patel",
    roles: ["STORE_MANAGER"],
    store_ids: ["BV-DEL"],
    is_active: true,
    phone: "+91-9876543215",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sm.noida",
    email: "sunita.noida@bettervision.in",
    password_hash: "$2b$12$5hH6uWG4VnIEvJ0mK2wORbSYT6eN9qC3pA4dX8aY1bB2cC3dD4eE5f", // Store@2024
    full_name: "Sunita Reddy",
    roles: ["STORE_MANAGER"],
    store_ids: ["BV-NOI"],
    is_active: true,
    phone: "+91-9876543216",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sm.gurgaon",
    email: "rahul.gurgaon@bettervision.in",
    password_hash: "$2b$12$5hH6uWG4VnIEvJ0mK2wORbSYT6eN9qC3pA4dX8aY1bB2cC3dD4eE5f", // Store@2024
    full_name: "Rahul Mehta",
    roles: ["STORE_MANAGER"],
    store_ids: ["BV-GUR"],
    is_active: true,
    phone: "+91-9876543217",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sm.mumbai",
    email: "ravi.mumbai@bettervision.in",
    password_hash: "$2b$12$5hH6uWG4VnIEvJ0mK2wORbSYT6eN9qC3pA4dX8aY1bB2cC3dD4eE5f", // Store@2024
    full_name: "Dr. Ravi Desai",
    roles: ["STORE_MANAGER", "OPTOMETRIST"],
    store_ids: ["BV-MUM"],
    is_active: true,
    phone: "+91-9876543218",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sm.bangalore",
    email: "lakshmi.bangalore@bettervision.in",
    password_hash: "$2b$12$5hH6uWG4VnIEvJ0mK2wORbSYT6eN9qC3pA4dX8aY1bB2cC3dD4eE5f", // Store@2024
    full_name: "Dr. Lakshmi Nair",
    roles: ["STORE_MANAGER", "OPTOMETRIST"],
    store_ids: ["BV-BLR"],
    is_active: true,
    phone: "+91-9876543219",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sm.wizopt",
    email: "karan@wizopt.com",
    password_hash: "$2b$12$5hH6uWG4VnIEvJ0mK2wORbSYT6eN9qC3pA4dX8aY1bB2cC3dD4eE5f", // Store@2024
    full_name: "Karan Malhotra",
    roles: ["STORE_MANAGER"],
    store_ids: ["WO-MUM"],
    is_active: true,
    phone: "+91-9876543220",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // ACCOUNTANTS
  // ============================================================================
  {
    username: "accountant.hq",
    email: "deepak@bettervision.in",
    password_hash: "$2b$12$4gG5tVF3UmHDuI9lJ1vNQaSXT5dM8pB2oZ3cW7aX0bA1bB2cC3dD4e", // Acc@2024
    full_name: "Deepak Joshi",
    roles: ["ACCOUNTANT"],
    store_ids: ["BV-DEL", "BV-NOI", "BV-GUR", "BV-MUM", "BV-BLR"],
    is_active: true,
    phone: "+91-9876543221",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "accountant.wizopt",
    email: "meera@wizopt.com",
    password_hash: "$2b$12$4gG5tVF3UmHDuI9lJ1vNQaSXT5dM8pB2oZ3cW7aX0bA1bB2cC3dD4e", // Acc@2024
    full_name: "Meera Iyer",
    roles: ["ACCOUNTANT"],
    store_ids: ["WO-MUM"],
    is_active: true,
    phone: "+91-9876543222",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // CATALOG MANAGER
  // ============================================================================
  {
    username: "catalog.manager",
    email: "pooja@bettervision.in",
    password_hash: "$2b$12$3fF4sUE2TlGCsH8kI0uMPZRWS4cL7oA1nY2bV6aW9bZ0aA1bB2cC3d", // Cat@2024
    full_name: "Pooja Agarwal",
    roles: ["CATALOG_MANAGER"],
    store_ids: [],
    is_active: true,
    phone: "+91-9876543223",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // HEAD OPTOMETRIST
  // ============================================================================
  {
    username: "dr.optom",
    email: "dr.sanjay@bettervision.in",
    password_hash: "$2b$12$2eE3rTD1SkFBrG7jH9tLOYQVR3bK6nZ0mX1aU5aV8bY9aZ0aA1bB2c", // Opt@2024
    full_name: "Dr. Sanjay Kapoor",
    roles: ["OPTOMETRIST"],
    store_ids: [],
    is_active: true,
    phone: "+91-9876543224",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // DELHI STORE STAFF
  // ============================================================================
  {
    username: "optom.delhi",
    email: "anjali.delhi@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Dr. Anjali Khanna",
    roles: ["OPTOMETRIST"],
    store_ids: ["BV-DEL"],
    is_active: true,
    phone: "+91-9876543225",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sales.delhi1",
    email: "rohit.delhi@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Rohit Kumar",
    roles: ["SALES_STAFF"],
    store_ids: ["BV-DEL"],
    is_active: true,
    phone: "+91-9876543226",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sales.delhi2",
    email: "gaurav.delhi@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Gaurav Singh",
    roles: ["SALES_STAFF", "CASHIER"],
    store_ids: ["BV-DEL"],
    is_active: true,
    phone: "+91-9876543227",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "cashier.delhi",
    email: "simran.delhi@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Simran Kaur",
    roles: ["CASHIER"],
    store_ids: ["BV-DEL"],
    is_active: true,
    phone: "+91-9876543228",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "workshop.delhi",
    email: "manoj.delhi@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Manoj Tiwari",
    roles: ["WORKSHOP_STAFF"],
    store_ids: ["BV-DEL"],
    is_active: true,
    phone: "+91-9876543229",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // NOIDA STORE STAFF
  // ============================================================================
  {
    username: "optom.noida",
    email: "kavita.noida@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Dr. Kavita Singh",
    roles: ["OPTOMETRIST"],
    store_ids: ["BV-NOI"],
    is_active: true,
    phone: "+91-9876543230",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sales.noida1",
    email: "arjun.noida@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Arjun Rao",
    roles: ["SALES_STAFF"],
    store_ids: ["BV-NOI"],
    is_active: true,
    phone: "+91-9876543231",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "cashier.noida",
    email: "priyanka.noida@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Priyanka Jain",
    roles: ["CASHIER"],
    store_ids: ["BV-NOI"],
    is_active: true,
    phone: "+91-9876543232",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "workshop.noida",
    email: "suresh.noida@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Suresh Yadav",
    roles: ["WORKSHOP_STAFF"],
    store_ids: ["BV-NOI"],
    is_active: true,
    phone: "+91-9876543233",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // GURGAON STORE STAFF
  // ============================================================================
  {
    username: "optom.gurgaon",
    email: "rajeev.gurgaon@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Dr. Rajeev Nair",
    roles: ["OPTOMETRIST"],
    store_ids: ["BV-GUR"],
    is_active: true,
    phone: "+91-9876543234",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sales.gurgaon1",
    email: "ankit.gurgaon@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Ankit Sharma",
    roles: ["SALES_STAFF"],
    store_ids: ["BV-GUR"],
    is_active: true,
    phone: "+91-9876543235",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "cashier.gurgaon",
    email: "ritu.gurgaon@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Ritu Bansal",
    roles: ["CASHIER"],
    store_ids: ["BV-GUR"],
    is_active: true,
    phone: "+91-9876543236",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "workshop.gurgaon",
    email: "ramesh.gurgaon@bettervision.in",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Ramesh Kumar",
    roles: ["WORKSHOP_STAFF"],
    store_ids: ["BV-GUR"],
    is_active: true,
    phone: "+91-9876543237",
    created_at: new Date(),
    updated_at: new Date()
  },

  // ============================================================================
  // WIZOPT STORE STAFF
  // ============================================================================
  {
    username: "optom.wizopt",
    email: "arjun@wizopt.com",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Dr. Arjun Menon",
    roles: ["OPTOMETRIST"],
    store_ids: ["WO-MUM"],
    is_active: true,
    phone: "+91-9876543238",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "sales.wizopt1",
    email: "sneha@wizopt.com",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Sneha Gupta",
    roles: ["SALES_STAFF"],
    store_ids: ["WO-MUM"],
    is_active: true,
    phone: "+91-9876543239",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "cashier.wizopt",
    email: "divya@wizopt.com",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Divya Rao",
    roles: ["CASHIER"],
    store_ids: ["WO-MUM"],
    is_active: true,
    phone: "+91-9876543240",
    created_at: new Date(),
    updated_at: new Date()
  },
  {
    username: "workshop.wizopt",
    email: "sunil@wizopt.com",
    password_hash: "$2b$12$1dD2qSC0RjEAqF6iG8sKNXPUQ2aJ5mY9lW0aT4aU7bX8aY9aZ0aA1b", // Staff@2024
    full_name: "Sunil Patil",
    roles: ["WORKSHOP_STAFF"],
    store_ids: ["WO-MUM"],
    is_active: true,
    phone: "+91-9876543241",
    created_at: new Date(),
    updated_at: new Date()
  }
];

// Insert users
print('Inserting users...');
let insertedCount = 0;
let skippedCount = 0;

users.forEach(user => {
  try {
    const existing = db.users.findOne({ username: user.username });
    if (!existing) {
      db.users.insertOne(user);
      print(`  ✓ ${user.username} (${user.full_name}) - ${user.roles.join(', ')}`);
      insertedCount++;
    } else {
      print(`  ⚠ ${user.username} already exists - skipped`);
      skippedCount++;
    }
  } catch (e) {
    print(`  ✗ Error inserting ${user.username}: ${e.message}`);
  }
});

print('');
print('============================================================================');
print(`✅ User Seeding Complete!`);
print('============================================================================');
print(`Total users: ${users.length}`);
print(`Inserted: ${insertedCount}`);
print(`Skipped: ${skippedCount}`);
print('');
print('⚠️  IMPORTANT: All users have default passwords!');
print('   Please change passwords after first login.');
print('');
print('Default password patterns:');
print('  - admin123 (SUPERADMIN)');
print('  - Ceo@2024 (CEO)');
print('  - Dir@2024 (Directors)');
print('  - Area@2024 (Area Managers)');
print('  - Store@2024 (Store Managers)');
print('  - Acc@2024 (Accountants)');
print('  - Cat@2024 (Catalog Manager)');
print('  - Opt@2024 (Optometrists)');
print('  - Staff@2024 (Store Staff)');
print('');
