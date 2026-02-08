# IMS 2.0 - Database & Scalability Optimization Strategy

## Phase 3: Database & Scalability (3 weeks, 3 developers + 1 DBA)

### Objectives
- Optimize MongoDB performance with strategic indexing
- Implement Redis caching layer
- Configure MongoDB Replica Set for high availability
- Setup API Gateway with rate limiting
- Reduce API response times by 50-70%
- Support 10,000+ concurrent users

---

## 1. MongoDB Schema Optimization

### 1.1 Index Strategy

#### Critical Indexes (Implement Immediately)
```javascript
// User collection - Authentication fast track
db.users.createIndex({ email: 1 }, { unique: true })
db.users.createIndex({ username: 1 }, { unique: true })
db.users.createIndex({ status: 1 })
db.users.createIndex({ "roles": 1 })
db.users.createIndex({ createdAt: 1 })
db.users.createIndex({ lastLogin: 1, status: 1 })

// Products collection - Search & filtering
db.products.createIndex({ sku: 1 }, { unique: true })
db.products.createIndex({ category: 1 })
db.products.createIndex({ status: 1, quantity: 1 })
db.products.createIndex({ "brand": 1, "category": 1 })
db.products.createIndex({ price: 1, quantity: 1 })
db.products.createIndex({ name: "text", description: "text" }) // Text search

// Orders collection - Fast queries
db.orders.createIndex({ customerId: 1, createdAt: -1 })
db.orders.createIndex({ orderNumber: 1 }, { unique: true })
db.orders.createIndex({ status: 1 })
db.orders.createIndex({ createdAt: -1 })
db.orders.createIndex({ paymentStatus: 1 })

// Inventory collection - Stock management
db.inventory.createIndex({ productId: 1, storeId: 1 }, { unique: true })
db.inventory.createIndex({ storeId: 1, quantity: 1 })
db.inventory.createIndex({ status: 1 })

// Prescriptions collection - Clinical data
db.prescriptions.createIndex({ patientId: 1, testDate: -1 })
db.prescriptions.createIndex({ customerId: 1 })
db.prescriptions.createIndex({ status: 1 })

// Workshop jobs collection
db.workshop_jobs.createIndex({ jobNumber: 1 }, { unique: true })
db.workshop_jobs.createIndex({ status: 1, priority: 1 })
db.workshop_jobs.createIndex({ orderId: 1 })
db.workshop_jobs.createIndex({ createdAt: -1 })
```

#### Performance Impact
- **Authentication**: ~200ms → ~50ms (75% reduction)
- **Product Search**: ~1500ms → ~300ms (80% reduction)
- **Order History**: ~800ms → ~100ms (87% reduction)
- **Inventory Lookup**: ~600ms → ~80ms (87% reduction)

### 1.2 Schema Denormalization Opportunities

**Before (Normalized)**:
```javascript
// Requires 3 database calls
db.orders.findOne({ _id: orderId })
db.customers.findOne({ _id: order.customerId })
db.products.find({ _id: { $in: order.productIds } })
```

**After (Denormalized)**:
```javascript
// Single database call with embedded data
{
  _id: ObjectId,
  orderNumber: "ORD-2026-001",
  customerName: "John Doe",
  customerPhone: "9876543210",
  items: [
    {
      productId: ObjectId,
      productName: "Frame Model X",
      sku: "SKU-123",
      quantity: 2,
      price: 2000
    }
  ]
}
```

### 1.3 Collection Partitioning Strategy

**Time-Series Data Partitioning**:
```javascript
// Instead of single large orders collection
db.orders_2026_01.createIndex({ createdAt: -1 })
db.orders_2026_02.createIndex({ createdAt: -1 })
db.orders_2026_03.createIndex({ createdAt: -1 })
// ... automatic archival of old months
```

**Benefits**:
- Smaller index sizes
- Faster queries on recent data
- Easy cleanup of old data
- Parallel scanning

---

## 2. Redis Caching Layer

### 2.1 Cache Strategy (Cache-Aside Pattern)

```typescript
// Pseudocode for API endpoint
async function getProduct(productId: string) {
  // 1. Check Redis cache
  const cacheKey = `product:${productId}`;
  const cached = await redis.get(cacheKey);
  if (cached) return JSON.parse(cached);

  // 2. Cache miss - query database
  const product = await db.products.findOne({ _id: productId });

  // 3. Store in cache with 5-minute TTL
  await redis.setex(cacheKey, 300, JSON.stringify(product));

  return product;
}
```

### 2.2 Cache Key Structure

```
product:{productId}                    // Product details
product:sku:{sku}                      // Product by SKU
category:{categoryId}:products         // Category products list
store:{storeId}:inventory              // Store inventory
user:{userId}:profile                  // User profile
order:{orderId}                        // Order details
customer:{customerId}:orders:recent    // Recent customer orders
```

### 2.3 Cache Invalidation Triggers

```typescript
// When product is updated, invalidate related caches
async function updateProduct(productId: string, data: any) {
  const product = await db.products.findByIdAndUpdate(productId, data);

  // Invalidate caches
  await redis.del(`product:${productId}`);
  await redis.del(`product:sku:${product.sku}`);
  await redis.del(`category:${product.category}:products`);

  return product;
}
```

### 2.4 Cache Warming Strategy

```typescript
// Pre-load frequently accessed data on startup
async function warmCache() {
  // 1. Cache all active categories
  const categories = await db.categories.find({ status: 'active' });
  for (const cat of categories) {
    const products = await db.products.find({ category: cat._id });
    await redis.setex(
      `category:${cat._id}:products`,
      3600, // 1 hour
      JSON.stringify(products)
    );
  }

  // 2. Cache bestselling products
  const bestsellers = await db.products
    .find({ status: 'active' })
    .sort({ sales: -1 })
    .limit(100);
  await redis.setex('bestsellers', 3600, JSON.stringify(bestsellers));

  // 3. Cache dashboard metrics
  const stats = await calculateDashboardStats();
  await redis.setex('dashboard:stats', 600, JSON.stringify(stats));
}
```

### 2.5 Cache Hit Rate Targets

| Cache Type | Target Hit Rate | TTL |
|-----------|-----------------|-----|
| Products | 85% | 5-15 minutes |
| Users | 90% | 30 minutes |
| Orders | 70% | 5-10 minutes |
| Categories | 95% | 1 hour |
| Dashboard Stats | 80% | 10 minutes |
| **Overall** | **85%** | - |

---

## 3. MongoDB Replica Set Configuration

### 3.1 Setup (3-Node Cluster)

```bash
# Primary node
mongod --replSet "ims-rs" --port 27017

# Secondary node 1
mongod --replSet "ims-rs" --port 27018

# Secondary node 2
mongod --replSet "ims-rs" --port 27019

# Arbiter (lightweight - no data)
mongod --replSet "ims-rs" --port 27020
```

### 3.2 Replica Set Initialization

```javascript
// Connect to primary and initiate
rs.initiate({
  _id: "ims-rs",
  members: [
    { _id: 0, host: "primary:27017", priority: 3 },
    { _id: 1, host: "secondary1:27018", priority: 2 },
    { _id: 2, host: "secondary2:27019", priority: 1 },
    { _id: 3, host: "arbiter:27020", arbiterOnly: true }
  ]
})

// Verify replica set status
rs.status()
```

### 3.3 Read Preference Strategy

```typescript
// API configuration
const mongoClient = new MongoClient(uri, {
  replicaSet: 'ims-rs',
  readPreference: 'secondaryPreferred', // Read from secondary if available
  w: 'majority', // Write acknowledged by majority
  retryWrites: true,
  maxPoolSize: 100
});

// Override for specific reads
const { Product } = db;

// High-traffic reads (search, browse)
Product.find({ status: 'active' })
  .hint({ category: 1, price: 1 })
  .read('secondary'); // Off-load to secondary

// Critical reads (order confirmation)
Order.findOne({ _id: orderId })
  .read('primary'); // Must read from primary
```

### 3.4 Failover & High Availability

- **Automatic Failover**: Primary failure → Secondary promoted (10-30 seconds)
- **Rolling Restarts**: Update secondaries first, then primary
- **Backup Strategy**: Daily snapshots on secondary, monthly full backups
- **RPO Target**: < 5 minutes (with journaling)
- **RTO Target**: < 1 minute (automatic failover)

---

## 4. API Gateway Setup (Kong/Nginx)

### 4.1 Rate Limiting Configuration

```nginx
# Nginx rate limiting example
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=100r/s;

server {
    listen 443 ssl http2;

    location ~ ^/api/v1/ {
        limit_req zone=api_limit burst=200 nodelay;

        # Route to backend
        proxy_pass http://backend_cluster;

        # Add rate limit headers
        add_header X-RateLimit-Limit 100;
        add_header X-RateLimit-Remaining $limit_conn_status;
    }
}
```

### 4.2 Request Routing

```nginx
# Route products API to read replicas
location ~ ^/api/v1/products {
    proxy_pass http://product_replica_cluster; # Secondary nodes
    proxy_read_timeout 5s;
    proxy_connect_timeout 2s;
}

# Route orders to primary
location ~ ^/api/v1/orders {
    proxy_pass http://backend_primary; # Primary node
    proxy_read_timeout 10s;
}

# Route health checks
location ~ ^/health {
    access_log off;
    proxy_pass http://backend_cluster;
}
```

### 4.3 Caching at Gateway Level

```nginx
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=api_cache:100m max_size=1g inactive=1h;

location ~ ^/api/v1/products {
    proxy_cache api_cache;
    proxy_cache_valid 200 5m;
    proxy_cache_key "$scheme$request_method$host$request_uri$cookie_user";
    add_header X-Cache-Status $upstream_cache_status;
}
```

---

## 5. Performance Targets

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| P95 Response Time | 1500ms | 400ms | 73% ↓ |
| P99 Response Time | 3000ms | 800ms | 73% ↓ |
| Cache Hit Rate | 20% | 85% | +425% |
| Concurrent Users | 1,000 | 10,000 | +900% |
| Queries/sec | 1,000 | 5,000 | +400% |
| Database CPU | 85% | 45% | 47% ↓ |

---

## 6. Implementation Timeline

### Week 1: Database Optimization
- [ ] Create all strategic indexes (2 days)
- [ ] Run EXPLAIN on slow queries (1 day)
- [ ] Denormalize high-traffic collections (2 days)
- [ ] Test index performance (1 day)

### Week 2: Redis & Caching
- [ ] Setup Redis cluster (1 day)
- [ ] Implement cache-aside pattern (3 days)
- [ ] Cache invalidation strategies (2 days)
- [ ] Load test caching (1 day)

### Week 3: High Availability
- [ ] Setup MongoDB Replica Set (2 days)
- [ ] Setup API Gateway (2 days)
- [ ] Failover testing (1 day)
- [ ] Performance benchmarking (1 day)

---

## 7. Monitoring & Alerts

### Key Metrics to Monitor
- **MongoDB**: Index hit rate, replication lag, oplog size
- **Redis**: Hit rate, memory usage, evictions, latency
- **API**: Response time P95/P99, throughput, error rate
- **System**: CPU, memory, disk I/O, network

### Alert Thresholds
- Replication lag > 1 second
- Cache hit rate < 75%
- P95 response time > 500ms
- Error rate > 1%
- Redis memory > 80%

---

## 8. Expected Outcomes

✅ **Performance**: 70% faster API responses
✅ **Scalability**: Support 10x more concurrent users
✅ **Reliability**: 99.95% uptime with automatic failover
✅ **Cost Efficiency**: 40% reduction in database load
