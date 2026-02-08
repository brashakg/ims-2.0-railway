# IMS 2.0 - API Documentation

## Complete REST API Reference

**Base URL**: `https://api.ims-2.0.com/api/v1`
**Authentication**: JWT Bearer Token
**Content-Type**: `application/json`

---

## Table of Contents

1. [Authentication](#authentication)
2. [Products](#products)
3. [Customers](#customers)
4. [Orders](#orders)
5. [Inventory](#inventory)
6. [Financial](#financial)
7. [Users](#users)
8. [Error Handling](#error-handling)
9. [Rate Limiting](#rate-limiting)
10. [Webhooks](#webhooks)

---

## Authentication

### Login
**POST** `/auth/login`

Request:
```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response (200):
```json
{
  "success": true,
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "refresh-token-123",
    "user": {
      "id": "user-123",
      "email": "user@example.com",
      "roles": ["ADMIN"],
      "store_id": "store-1"
    }
  }
}
```

### Refresh Token
**POST** `/auth/refresh`

Request:
```json
{
  "refresh_token": "refresh-token-123"
}
```

Response (200):
```json
{
  "success": true,
  "data": {
    "token": "new-jwt-token",
    "refresh_token": "new-refresh-token"
  }
}
```

### Logout
**POST** `/auth/logout`

Headers: `Authorization: Bearer <token>`

Response (200):
```json
{
  "success": true,
  "message": "Logged out successfully"
}
```

### Get Profile
**GET** `/auth/profile`

Headers: `Authorization: Bearer <token>`

Response (200):
```json
{
  "success": true,
  "data": {
    "id": "user-123",
    "email": "user@example.com",
    "full_name": "John Doe",
    "roles": ["ADMIN"],
    "store_id": "store-1",
    "is_2fa_enabled": true,
    "created_at": "2026-01-01T10:00:00Z"
  }
}
```

### Enable 2FA
**POST** `/auth/2fa/enable`

Headers: `Authorization: Bearer <token>`

Request:
```json
{
  "password": "current_password"
}
```

Response (200):
```json
{
  "qr_code": "data:image/png;base64,...",
  "secret_key": "JBSWY3DPEBLW64TMMQ",
  "backup_codes": ["abc12345", "def67890", "ghi34567", ...]
}
```

---

## Products

### List Products
**GET** `/products?page=1&limit=20&category=FRAMES&search=ray`

Query Parameters:
- `page` (int): Page number (default: 1)
- `limit` (int): Items per page (default: 20)
- `category` (string): Filter by category
- `brand` (string): Filter by brand
- `search` (string): Search by name/description
- `status` (string): ACTIVE, INACTIVE
- `sort` (string): name, price, created_at
- `order` (string): asc, desc

Response (200):
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "prod-1",
        "sku": "FR-001",
        "name": "Classic Metal Frame",
        "description": "Timeless design",
        "category": "FRAMES",
        "brand": "BrandA",
        "price": 2500,
        "quantity": 150,
        "status": "ACTIVE",
        "imageUrl": "https://...",
        "created_at": "2026-01-01T10:00:00Z"
      }
    ],
    "total": 156,
    "page": 1,
    "pageSize": 20,
    "totalPages": 8
  }
}
```

### Get Product
**GET** `/products/{id}`

Response (200):
```json
{
  "success": true,
  "data": {
    "id": "prod-1",
    "sku": "FR-001",
    "name": "Classic Metal Frame",
    "description": "Timeless design",
    "category": "FRAMES",
    "brand": "BrandA",
    "price": 2500,
    "quantity": 150,
    "status": "ACTIVE",
    "images": ["url1", "url2"],
    "specifications": {
      "material": "Titanium",
      "weight": "18g",
      "colors": ["Black", "Silver"]
    },
    "created_at": "2026-01-01T10:00:00Z",
    "updated_at": "2026-02-08T15:30:00Z"
  }
}
```

### Create Product
**POST** `/products`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `product:create`

Request:
```json
{
  "sku": "FR-002",
  "name": "New Frame",
  "description": "Modern design",
  "category": "FRAMES",
  "brand": "BrandB",
  "price": 3000,
  "quantity": 100,
  "status": "ACTIVE",
  "specifications": {
    "material": "Acetate",
    "weight": "20g"
  }
}
```

Response (201):
```json
{
  "success": true,
  "data": {
    "id": "prod-999",
    "sku": "FR-002",
    "name": "New Frame",
    ...
  }
}
```

### Update Product
**PUT** `/products/{id}`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `product:update`

Request:
```json
{
  "name": "Updated Frame Name",
  "price": 3200,
  "quantity": 120
}
```

Response (200):
```json
{
  "success": true,
  "data": { ... }
}
```

### Delete Product
**DELETE** `/products/{id}`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `product:delete`

Response (200):
```json
{
  "success": true,
  "message": "Product deleted successfully"
}
```

### Bulk Import Products
**POST** `/products/bulk-import`

Headers:
- `Authorization: Bearer <token>`
- `Content-Type: multipart/form-data`
- Required permission: `product:bulk_import`

Form Data:
- `file`: CSV/Excel file with product data

Response (202):
```json
{
  "success": true,
  "data": {
    "job_id": "import-job-123",
    "status": "processing",
    "message": "Import job queued"
  }
}
```

---

## Customers

### List Customers
**GET** `/customers?page=1&limit=20&status=ACTIVE&search=john`

Query Parameters:
- `page` (int)
- `limit` (int)
- `status` (string): ACTIVE, VIP, INACTIVE
- `search` (string): Search by name/email
- `store_id` (string): Filter by store
- `sort` (string): name, created_at, total_spent

Response (200):
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "cust-1",
        "firstName": "John",
        "lastName": "Doe",
        "email": "john@example.com",
        "phone": "9876543210",
        "address": "123 Main St",
        "city": "City",
        "state": "State",
        "zipCode": "12345",
        "status": "VIP",
        "totalOrders": 25,
        "totalSpent": 150000,
        "createdAt": "2026-01-01T10:00:00Z"
      }
    ],
    "total": 450,
    "page": 1,
    "pageSize": 20,
    "totalPages": 23
  }
}
```

### Create Customer
**POST** `/customers`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `customer:create`

Request:
```json
{
  "firstName": "Jane",
  "lastName": "Smith",
  "email": "jane@example.com",
  "phone": "9876543211",
  "address": "456 Oak Ave",
  "city": "City",
  "state": "State",
  "zipCode": "12346",
  "status": "ACTIVE"
}
```

Response (201):
```json
{
  "success": true,
  "data": {
    "id": "cust-999",
    "firstName": "Jane",
    ...
  }
}
```

### Update Customer
**PUT** `/customers/{id}`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `customer:update`

Request: (partial update)
```json
{
  "email": "newemail@example.com",
  "phone": "9876543212",
  "status": "VIP"
}
```

Response (200):
```json
{
  "success": true,
  "data": { ... }
}
```

### Export Customers
**GET** `/customers/export?format=csv&status=ACTIVE`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `customer:export`

Query Parameters:
- `format` (string): csv, excel, json
- `status` (string): Filter by status
- `date_range` (string): last_30_days, last_year, custom

Response (200): File download

---

## Orders

### List Orders
**GET** `/orders?page=1&limit=20&status=PENDING&store_id=store-1`

Query Parameters:
- `page` (int)
- `limit` (int)
- `status` (string): PENDING, COMPLETED, CANCELLED
- `payment_status` (string): PENDING, PAID, REFUNDED
- `store_id` (string)
- `customer_id` (string)
- `date_from` (string): ISO date
- `date_to` (string): ISO date

Response (200):
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "ord-1",
        "orderNumber": "ORD-2026-001",
        "customerId": "cust-1",
        "customerName": "John Doe",
        "storeId": "store-1",
        "totalAmount": 5500,
        "paymentStatus": "PENDING",
        "status": "PENDING",
        "items": [
          {
            "productId": "prod-1",
            "productName": "Classic Metal Frame",
            "quantity": 2,
            "price": 2500,
            "subtotal": 5000
          }
        ],
        "createdAt": "2026-02-08T10:00:00Z"
      }
    ],
    "total": 45,
    "page": 1,
    "pageSize": 20,
    "totalPages": 3
  }
}
```

### Create Order
**POST** `/orders`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `order:create`

Request:
```json
{
  "customerId": "cust-1",
  "storeId": "store-1",
  "items": [
    {
      "productId": "prod-1",
      "quantity": 2
    },
    {
      "productId": "prod-3",
      "quantity": 1
    }
  ],
  "notes": "Customer requested expedited shipping"
}
```

Response (201):
```json
{
  "success": true,
  "data": {
    "id": "ord-999",
    "orderNumber": "ORD-2026-999",
    "customerId": "cust-1",
    "storeId": "store-1",
    "totalAmount": 5500,
    "paymentStatus": "PENDING",
    "status": "PENDING",
    "items": [ ... ],
    "createdAt": "2026-02-08T15:30:00Z"
  }
}
```

### Process Payment
**POST** `/orders/{id}/payment`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `order:process_payment`

Request:
```json
{
  "paymentMethod": "CARD",
  "amount": 5500,
  "cardToken": "tok_123456",
  "notes": "Payment approved"
}
```

Response (200):
```json
{
  "success": true,
  "data": {
    "transactionId": "txn-123",
    "paymentStatus": "PAID",
    "amount": 5500,
    "timestamp": "2026-02-08T15:35:00Z"
  }
}
```

---

## Inventory

### List Inventory
**GET** `/inventory?storeId=store-1&page=1&limit=20`

Query Parameters:
- `storeId` (string): Required
- `page` (int)
- `limit` (int)
- `status` (string): IN_STOCK, LOW_STOCK, OUT_OF_STOCK

Response (200):
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "inv-1",
        "productId": "prod-1",
        "storeId": "store-1",
        "quantity": 150,
        "minStock": 20,
        "maxStock": 300,
        "status": "IN_STOCK",
        "lastRestockDate": "2026-02-01T10:00:00Z"
      }
    ],
    "total": 156,
    "page": 1,
    "pageSize": 20,
    "totalPages": 8
  }
}
```

### Update Inventory
**PUT** `/inventory/{id}`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `inventory:update`

Request:
```json
{
  "quantity": 175,
  "reason": "Restock received"
}
```

Response (200):
```json
{
  "success": true,
  "data": { ... }
}
```

### Transfer Inventory
**POST** `/inventory/transfer`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `inventory:transfer`

Request:
```json
{
  "from_store_id": "store-1",
  "to_store_id": "store-2",
  "items": [
    {
      "product_id": "prod-1",
      "quantity": 20
    }
  ],
  "reason": "Stock rebalancing"
}
```

Response (201):
```json
{
  "success": true,
  "data": {
    "transfer_id": "transfer-123",
    "status": "PENDING",
    "items": [ ... ]
  }
}
```

---

## Financial

### List Invoices
**GET** `/invoices?page=1&status=PAID&date_from=2026-01-01`

Response (200):
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "inv-1",
        "invoiceNumber": "INV-2026-001",
        "customerId": "cust-1",
        "orderId": "ord-1",
        "amount": 5500,
        "status": "PAID",
        "dueDate": "2026-02-28T00:00:00Z",
        "paidDate": "2026-02-10T10:00:00Z",
        "createdAt": "2026-02-08T10:00:00Z"
      }
    ],
    "total": 156,
    "totalAmount": 2500000
  }
}
```

### Get Dashboard Stats
**GET** `/reports/dashboard-stats`

Headers: `Authorization: Bearer <token>`

Response (200):
```json
{
  "success": true,
  "data": {
    "totalSales": 2500000,
    "totalOrders": 156,
    "activeCustomers": 450,
    "totalRevenue": 2400000,
    "ordersFulfilled": 140,
    "pendingOrders": 16,
    "averageOrderValue": 16025,
    "conversionRate": 12.5,
    "topProducts": [
      {
        "id": "prod-1",
        "name": "Classic Metal Frame",
        "sales": 250000
      }
    ],
    "revenueByMonth": [
      { "month": "January", "revenue": 800000 },
      { "month": "February", "revenue": 1700000 }
    ]
  }
}
```

---

## Users

### List Users
**GET** `/users?page=1&role=ADMIN`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `user:read`

Response (200):
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "user-1",
        "email": "admin@example.com",
        "firstName": "Admin",
        "lastName": "User",
        "roles": ["ADMIN"],
        "status": "ACTIVE",
        "createdAt": "2026-01-01T10:00:00Z",
        "lastLogin": "2026-02-08T14:30:00Z"
      }
    ],
    "total": 12,
    "page": 1,
    "pageSize": 20,
    "totalPages": 1
  }
}
```

### Create User
**POST** `/users`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `user:create`

Request:
```json
{
  "email": "newuser@example.com",
  "firstName": "New",
  "lastName": "User",
  "password": "SecurePassword123",
  "roles": ["SALES_STAFF"],
  "store_id": "store-1"
}
```

Response (201):
```json
{
  "success": true,
  "data": {
    "id": "user-123",
    "email": "newuser@example.com",
    ...
  }
}
```

### Update User Roles
**PUT** `/users/{id}/roles`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `user:manage_roles`

Request:
```json
{
  "roles": ["STORE_MANAGER", "INVENTORY_MANAGER"]
}
```

Response (200):
```json
{
  "success": true,
  "data": { ... }
}
```

---

## Error Handling

### Error Response Format

All errors follow this format:

```json
{
  "success": false,
  "error": "Error message",
  "code": "ERROR_CODE",
  "details": {
    "field": "email",
    "message": "Invalid email format"
  },
  "timestamp": "2026-02-08T15:30:00Z"
}
```

### Common Error Codes

| Code | HTTP | Description |
|------|------|-------------|
| `INVALID_REQUEST` | 400 | Bad request (validation error) |
| `UNAUTHORIZED` | 401 | Missing or invalid token |
| `FORBIDDEN` | 403 | Permission denied |
| `NOT_FOUND` | 404 | Resource not found |
| `CONFLICT` | 409 | Resource already exists |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Server error |

---

## Rate Limiting

**Limits**:
- Authenticated users: 100 requests/minute
- Unauthenticated: 20 requests/minute
- Login endpoint: 5 attempts/minute per IP

**Rate Limit Headers**:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1644341600
```

---

## Webhooks

### Register Webhook
**POST** `/webhooks`

Headers:
- `Authorization: Bearer <token>`
- Required permission: `system:admin`

Request:
```json
{
  "url": "https://your-app.com/webhooks/ims",
  "events": ["order.created", "payment.processed", "inventory.updated"],
  "active": true,
  "secret": "webhook-secret-key"
}
```

### Webhook Events

**order.created**:
```json
{
  "event": "order.created",
  "data": {
    "id": "ord-123",
    "orderNumber": "ORD-2026-001",
    "customerId": "cust-1",
    "totalAmount": 5500,
    "createdAt": "2026-02-08T15:30:00Z"
  },
  "timestamp": "2026-02-08T15:30:00Z"
}
```

**payment.processed**:
```json
{
  "event": "payment.processed",
  "data": {
    "orderId": "ord-123",
    "transactionId": "txn-456",
    "amount": 5500,
    "status": "PAID"
  },
  "timestamp": "2026-02-08T15:35:00Z"
}
```

---

## OpenAPI/Swagger

Interactive API documentation available at:
```
https://api.ims-2.0.com/docs
https://api.ims-2.0.com/redoc
```

---

**Last Updated**: February 8, 2026
**API Version**: 1.0
**Authentication**: JWT Bearer Token
