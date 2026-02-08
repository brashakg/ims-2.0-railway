// ============================================================================
// IMS 2.0 - Test Fixtures & Mock Data
// ============================================================================
// Reusable mock data for unit and integration tests

import {
  User,
  Product,
  Customer,
  Order,
  Prescription,
  Store,
  Inventory,
  WorkshopJob,
} from '../types';

/**
 * Mock User fixtures
 */
export const mockUsers = {
  admin: {
    id: 'user-1',
    email: 'admin@ims.local',
    username: 'admin',
    firstName: 'Admin',
    lastName: 'User',
    roles: ['ADMIN'],
    status: 'ACTIVE',
    createdAt: new Date('2026-01-01'),
    lastLogin: new Date('2026-02-08'),
  } as User,

  manager: {
    id: 'user-2',
    email: 'manager@ims.local',
    username: 'manager',
    firstName: 'Manager',
    lastName: 'User',
    roles: ['STORE_MANAGER'],
    status: 'ACTIVE',
    createdAt: new Date('2026-01-15'),
    lastLogin: new Date('2026-02-07'),
  } as User,

  staff: {
    id: 'user-3',
    email: 'staff@ims.local',
    username: 'staff',
    firstName: 'Staff',
    lastName: 'User',
    roles: ['STAFF'],
    status: 'ACTIVE',
    createdAt: new Date('2026-02-01'),
    lastLogin: new Date('2026-02-08'),
  } as User,

  inactive: {
    id: 'user-4',
    email: 'inactive@ims.local',
    username: 'inactive',
    firstName: 'Inactive',
    lastName: 'User',
    roles: ['STAFF'],
    status: 'INACTIVE',
    createdAt: new Date('2025-12-01'),
    lastLogin: new Date('2025-12-15'),
  } as User,
};

/**
 * Mock Product fixtures
 */
export const mockProducts = {
  frame1: {
    id: 'prod-1',
    sku: 'FR-001',
    name: 'Classic Metal Frame',
    description: 'Timeless metal frame design',
    category: 'FRAMES',
    brand: 'BrandA',
    price: 2500,
    quantity: 150,
    status: 'ACTIVE',
    imageUrl: 'https://example.com/frame1.jpg',
  } as Product,

  lens1: {
    id: 'prod-2',
    sku: 'LE-001',
    name: 'Single Vision Lens',
    description: 'Clear single vision lens',
    category: 'LENSES',
    brand: 'LensGo',
    price: 1500,
    quantity: 300,
    status: 'ACTIVE',
    imageUrl: 'https://example.com/lens1.jpg',
  } as Product,

  coating1: {
    id: 'prod-3',
    sku: 'CT-001',
    name: 'Anti-Reflective Coating',
    description: 'Premium anti-reflective coating',
    category: 'COATINGS',
    brand: 'CoatTech',
    price: 500,
    quantity: 500,
    status: 'ACTIVE',
    imageUrl: 'https://example.com/coating1.jpg',
  } as Product,

  outOfStock: {
    id: 'prod-4',
    sku: 'FR-002',
    name: 'Out of Stock Frame',
    description: 'This product is out of stock',
    category: 'FRAMES',
    brand: 'BrandB',
    price: 3000,
    quantity: 0,
    status: 'INACTIVE',
    imageUrl: 'https://example.com/frame2.jpg',
  } as Product,
};

/**
 * Mock Customer fixtures
 */
export const mockCustomers = {
  regular: {
    id: 'cust-1',
    firstName: 'John',
    lastName: 'Doe',
    email: 'john.doe@example.com',
    phone: '9876543210',
    address: '123 Main St, City',
    city: 'City',
    state: 'State',
    zipCode: '12345',
    status: 'ACTIVE',
    totalOrders: 5,
    totalSpent: 25000,
    createdAt: new Date('2025-06-01'),
  } as Customer,

  vip: {
    id: 'cust-2',
    firstName: 'Jane',
    lastName: 'Smith',
    email: 'jane.smith@example.com',
    phone: '9876543211',
    address: '456 Oak Ave, City',
    city: 'City',
    state: 'State',
    zipCode: '12346',
    status: 'VIP',
    totalOrders: 25,
    totalSpent: 150000,
    createdAt: new Date('2024-01-01'),
  } as Customer,

  new: {
    id: 'cust-3',
    firstName: 'Bob',
    lastName: 'Johnson',
    email: 'bob.johnson@example.com',
    phone: '9876543212',
    address: '789 Elm St, City',
    city: 'City',
    state: 'State',
    zipCode: '12347',
    status: 'ACTIVE',
    totalOrders: 1,
    totalSpent: 5000,
    createdAt: new Date('2026-02-01'),
  } as Customer,
};

/**
 * Mock Order fixtures
 */
export const mockOrders = {
  pending: {
    id: 'ord-1',
    orderNumber: 'ORD-2026-001',
    customerId: 'cust-1',
    customerName: 'John Doe',
    storeId: 'store-1',
    totalAmount: 5500,
    paymentStatus: 'PENDING',
    status: 'PENDING',
    items: [
      {
        productId: 'prod-1',
        productName: 'Classic Metal Frame',
        quantity: 2,
        price: 2500,
      },
      {
        productId: 'prod-3',
        productName: 'Anti-Reflective Coating',
        quantity: 1,
        price: 500,
      },
    ],
    createdAt: new Date('2026-02-08'),
    updatedAt: new Date('2026-02-08'),
  } as Order,

  completed: {
    id: 'ord-2',
    orderNumber: 'ORD-2026-002',
    customerId: 'cust-2',
    customerName: 'Jane Smith',
    storeId: 'store-1',
    totalAmount: 4000,
    paymentStatus: 'PAID',
    status: 'COMPLETED',
    items: [
      {
        productId: 'prod-2',
        productName: 'Single Vision Lens',
        quantity: 2,
        price: 1500,
      },
    ],
    createdAt: new Date('2026-02-07'),
    updatedAt: new Date('2026-02-08'),
  } as Order,

  cancelled: {
    id: 'ord-3',
    orderNumber: 'ORD-2026-003',
    customerId: 'cust-3',
    customerName: 'Bob Johnson',
    storeId: 'store-1',
    totalAmount: 2500,
    paymentStatus: 'REFUNDED',
    status: 'CANCELLED',
    items: [
      {
        productId: 'prod-1',
        productName: 'Classic Metal Frame',
        quantity: 1,
        price: 2500,
      },
    ],
    createdAt: new Date('2026-02-05'),
    updatedAt: new Date('2026-02-06'),
  } as Order,
};

/**
 * Mock Prescription fixtures
 */
export const mockPrescriptions = {
  current: {
    id: 'rx-1',
    customerId: 'cust-1',
    prescriptionNumber: 'RX-2026-001',
    doctorName: 'Dr. Smith',
    prescriptionDate: new Date('2026-01-15'),
    expiryDate: new Date('2027-01-15'),
    sphereOD: -2.5,
    sphereOS: -2.0,
    cylinderOD: -0.5,
    cylinderOS: -0.75,
    axisOD: 180,
    axisOS: 175,
    status: 'ACTIVE',
  } as Prescription,

  expired: {
    id: 'rx-2',
    customerId: 'cust-2',
    prescriptionNumber: 'RX-2026-002',
    doctorName: 'Dr. Johnson',
    prescriptionDate: new Date('2024-01-15'),
    expiryDate: new Date('2025-01-15'),
    sphereOD: -3.0,
    sphereOS: -2.75,
    cylinderOD: -1.0,
    cylinderOS: -1.0,
    axisOD: 180,
    axisOS: 180,
    status: 'EXPIRED',
  } as Prescription,
};

/**
 * Mock Store fixtures
 */
export const mockStores = {
  mainStore: {
    id: 'store-1',
    name: 'Main Store',
    location: 'Downtown',
    city: 'City',
    state: 'State',
    zipCode: '12345',
    managerName: 'Manager User',
    phone: '9876543210',
    email: 'manager@store1.com',
    status: 'ACTIVE',
    createdAt: new Date('2025-01-01'),
  } as Store,

  branchStore: {
    id: 'store-2',
    name: 'Branch Store',
    location: 'Mall',
    city: 'City',
    state: 'State',
    zipCode: '12346',
    managerName: 'Branch Manager',
    phone: '9876543211',
    email: 'manager@store2.com',
    status: 'ACTIVE',
    createdAt: new Date('2025-06-01'),
  } as Store,
};

/**
 * Mock Inventory fixtures
 */
export const mockInventory = {
  available: {
    id: 'inv-1',
    productId: 'prod-1',
    storeId: 'store-1',
    quantity: 150,
    minStock: 20,
    maxStock: 300,
    status: 'IN_STOCK',
    lastRestockDate: new Date('2026-02-01'),
  } as Inventory,

  lowStock: {
    id: 'inv-2',
    productId: 'prod-2',
    storeId: 'store-2',
    quantity: 5,
    minStock: 20,
    maxStock: 200,
    status: 'LOW_STOCK',
    lastRestockDate: new Date('2026-01-15'),
  } as Inventory,

  empty: {
    id: 'inv-3',
    productId: 'prod-4',
    storeId: 'store-1',
    quantity: 0,
    minStock: 20,
    maxStock: 250,
    status: 'OUT_OF_STOCK',
    lastRestockDate: new Date('2025-12-01'),
  } as Inventory,
};

/**
 * Mock Workshop Job fixtures
 */
export const mockWorkshopJobs = {
  pending: {
    id: 'job-1',
    jobNumber: 'JOB-2026-001',
    orderId: 'ord-1',
    customerName: 'John Doe',
    frameModel: 'Classic Metal Frame',
    lensType: 'Single Vision',
    status: 'PENDING',
    priority: 'HIGH',
    assignedTo: 'staff',
    createdAt: new Date('2026-02-08'),
    dueDate: new Date('2026-02-10'),
  } as WorkshopJob,

  inProgress: {
    id: 'job-2',
    jobNumber: 'JOB-2026-002',
    orderId: 'ord-2',
    customerName: 'Jane Smith',
    frameModel: 'Modern Acetate Frame',
    lensType: 'Progressive',
    status: 'IN_PROGRESS',
    priority: 'MEDIUM',
    assignedTo: 'staff',
    createdAt: new Date('2026-02-07'),
    dueDate: new Date('2026-02-09'),
  } as WorkshopJob,

  completed: {
    id: 'job-3',
    jobNumber: 'JOB-2026-003',
    orderId: 'ord-3',
    customerName: 'Bob Johnson',
    frameModel: 'Titanium Frame',
    lensType: 'Single Vision',
    status: 'COMPLETED',
    priority: 'LOW',
    assignedTo: 'staff',
    createdAt: new Date('2026-02-01'),
    dueDate: new Date('2026-02-05'),
    completedAt: new Date('2026-02-05'),
  } as WorkshopJob,
};

/**
 * Mock API Response fixtures
 */
export const mockApiResponses = {
  loginSuccess: {
    success: true,
    data: {
      user: mockUsers.admin,
      token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...',
      refreshToken: 'refresh-token-123',
    },
  },

  loginError: {
    success: false,
    error: 'Invalid credentials',
  },

  productsList: {
    success: true,
    data: {
      items: [mockProducts.frame1, mockProducts.lens1],
      total: 2,
      page: 1,
      pageSize: 20,
      totalPages: 1,
    },
  },

  customersList: {
    success: true,
    data: {
      items: [mockCustomers.regular, mockCustomers.vip],
      total: 2,
      page: 1,
      pageSize: 20,
      totalPages: 1,
    },
  },

  ordersList: {
    success: true,
    data: {
      items: [mockOrders.pending, mockOrders.completed],
      total: 2,
      page: 1,
      pageSize: 20,
      totalPages: 1,
    },
  },

  dashboardStats: {
    success: true,
    data: {
      totalSales: 500000,
      totalOrders: 150,
      activeCustomers: 450,
      totalRevenue: 5000000,
      ordersFulfilled: 140,
      pendingOrders: 10,
      averageOrderValue: 33333,
      conversionRate: 12.5,
    },
  },
};

/**
 * Create a collection of fixtures with pagination
 */
export function createPaginatedFixture<T>(items: T[], page = 1, pageSize = 20) {
  const start = (page - 1) * pageSize;
  const end = start + pageSize;
  const paginatedItems = items.slice(start, end);

  return {
    items: paginatedItems,
    total: items.length,
    page,
    pageSize,
    totalPages: Math.ceil(items.length / pageSize),
  };
}

/**
 * Factory functions for creating custom fixtures
 */
export function createMockUser(overrides?: Partial<User>): User {
  return {
    ...mockUsers.staff,
    ...overrides,
  };
}

export function createMockProduct(overrides?: Partial<Product>): Product {
  return {
    ...mockProducts.frame1,
    ...overrides,
  };
}

export function createMockCustomer(overrides?: Partial<Customer>): Customer {
  return {
    ...mockCustomers.regular,
    ...overrides,
  };
}

export function createMockOrder(overrides?: Partial<Order>): Order {
  return {
    ...mockOrders.pending,
    ...overrides,
  };
}

export function createMockPrescription(overrides?: Partial<Prescription>): Prescription {
  return {
    ...mockPrescriptions.current,
    ...overrides,
  };
}

export function createMockStore(overrides?: Partial<Store>): Store {
  return {
    ...mockStores.mainStore,
    ...overrides,
  };
}

export function createMockWorkshopJob(overrides?: Partial<WorkshopJob>): WorkshopJob {
  return {
    ...mockWorkshopJobs.pending,
    ...overrides,
  };
}
