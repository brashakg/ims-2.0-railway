# IMS 2.0 - Architecture & Deployment Guide

## Enterprise Architecture Overview

**Last Updated**: February 8, 2026
**Version**: 1.0
**Status**: Production Ready

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Users / Clients                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────┐
        │     Vercel (Frontend CDN)           │
        │   - React application               │
        │   - Static assets                   │
        │   - Auto-scaling                    │
        └────────┬─────────────────────────────┘
                 │ HTTPS/TLS 1.3
                 ▼
    ┌────────────────────────────────────────┐
    │   AWS Application Load Balancer (ALB)  │
    │   - SSL termination                    │
    │   - Traffic routing                    │
    │   - Health checks                      │
    └────────┬─────────────────────────────────┘
             │ HTTP/1.1
             ▼
    ┌────────────────────────────────────────┐
    │   ECS Cluster (Backend Services)       │
    │  ┌──────────────────────────────────┐  │
    │  │ FastAPI Container 1              │  │
    │  │ - Port 8000                      │  │
    │  │ - Auto-restart                   │  │
    │  └──────────────────────────────────┘  │
    │  ┌──────────────────────────────────┐  │
    │  │ FastAPI Container 2              │  │
    │  │ - Port 8000                      │  │
    │  │ - Auto-restart                   │  │
    │  └──────────────────────────────────┘  │
    │  ┌──────────────────────────────────┐  │
    │  │ FastAPI Container 3              │  │
    │  │ - Port 8000                      │  │
    │  │ - Auto-restart                   │  │
    │  └──────────────────────────────────┘  │
    └────────┬─────────────────────────────────┘
             │
    ┌────────┴──────────────────────────────────┐
    │                                           │
    ▼                                           ▼
┌──────────────────────────┐      ┌──────────────────────────┐
│  RDS PostgreSQL 15       │      │  ElastiCache Redis       │
│  - Master (Primary)      │      │  - Cluster mode          │
│  - Replica (Standby)     │      │  - 3 nodes               │
│  - Automated backup      │      │  - Automatic failover    │
│  - Encryption at rest    │      │  - Encryption            │
└──────────────────────────┘      └──────────────────────────┘
```

---

## Component Details

### Frontend (Vercel)

**Technology Stack**:
- React 18+
- TypeScript
- Vite build tool
- TailwindCSS for styling

**Deployment**:
- Automatic deployment on git push to main
- Edge caching across global CDN
- Automatic SSL certificate management
- Auto-scaling based on traffic

**Environment Variables**:
```
VITE_API_URL=https://api.ims-2.0.com/api/v1
VITE_SENTRY_DSN=<sentry-dsn>
VITE_ANALYTICS_KEY=<analytics-key>
```

### Backend (ECS on AWS)

**Technology Stack**:
- Python 3.11
- FastAPI framework
- SQLAlchemy ORM
- Pydantic validation

**Container Details**:
- Base image: python:3.11-slim
- Port: 8000
- Memory: 512MB
- CPU: 256 units
- Task count: 3 (for HA)

**Health Check**:
```
GET /health
Interval: 30 seconds
Timeout: 10 seconds
Healthy threshold: 2 consecutive successes
```

### Database (RDS PostgreSQL)

**Configuration**:
- Version: PostgreSQL 15.4
- Instance class: db.t3.small (prod), db.t3.micro (dev)
- Storage: 100GB SSD, auto-scaling
- Backup retention: 30 days
- Multi-AZ: Enabled (production)
- Encryption: AWS KMS customer-managed

**Replication**:
- Read replicas in same region
- Automatic failover < 60 seconds
- Synchronous replication

### Cache (ElastiCache Redis)

**Configuration**:
- Version: Redis 7.0
- Node type: cache.t3.small (prod), cache.t3.micro (dev)
- Cluster: 3-node (production), 1-node (dev)
- Automatic failover: Enabled (production)
- Encryption: At-rest and in-transit

**Cache Strategy**:
- Cache-aside pattern
- TTL: 5 minutes (products), 15 minutes (users)
- Eviction: Least Recently Used (LRU)

---

## Data Flow

### User Login Request

```
1. Frontend -> POST /api/v1/auth/login
2. ALB routes to ECS instance
3. FastAPI validates credentials
4. Query PostgreSQL user table
5. Generate JWT token
6. Cache token in Redis (1 hour TTL)
7. Return token to frontend
8. Audit log event to PostgreSQL
9. Response < 500ms (P95)
```

### Product Search Request

```
1. Frontend -> GET /api/v1/products?search=frame
2. Check Redis cache (key: search:frame:page:1)
3. Cache HIT: Return cached results (< 10ms)
4. Cache MISS:
   - Query PostgreSQL with search parameter
   - Add results to Redis (5 min TTL)
   - Return results (< 100ms)
5. All requests logged to CloudWatch
```

### Order Creation Request

```
1. Frontend -> POST /api/v1/orders
2. Validate request with Pydantic schema
3. Check user permission (RBAC)
4. Create order in PostgreSQL transaction
5. Add order items
6. Update inventory (decrement stock)
7. Invalidate cache (inventory keys)
8. Log audit event
9. Publish webhook event
10. Return order with order number
11. Audit trail immutable in database
```

---

## Deployment Pipeline

### Development -> Staging -> Production

```
Branch Creation
      │
      ▼
GitHub Push
      │
      ▼
GitHub Actions Trigger
      │
      ├─→ Unit Tests (Jest, Pytest)
      │
      ├─→ Integration Tests
      │
      ├─→ Security Scan (npm audit, Bandit)
      │
      ├─→ Build Application
      │   ├─→ Frontend: npm run build → Vite
      │   └─→ Backend: docker build → ECR
      │
      ├─→ Upload Artifacts
      │
      └─→ Deploy (if on main branch)
          ├─→ Frontend → Vercel
          ├─→ Backend → ECS
          └─→ Smoke Tests
```

### Rollback Procedure

```
If production issue detected:
  1. Check CloudWatch logs for error pattern
  2. Run: git revert HEAD
  3. Run: git push origin main
  4. GitHub Actions auto-redeploys previous version
  5. Verify health checks passing
  6. Post incident retrospective
```

---

## Data Consistency & Transactions

### ACID Compliance

**PostgreSQL ACID guarantees**:
- Atomicity: Entire transaction succeeds or fails
- Consistency: Database constraints always valid
- Isolation: Concurrent transactions don't interfere
- Durability: Committed data survives failure

**Example: Order + Payment Transaction**:
```python
async with db.transaction():
    # Both succeed or both fail atomically
    order = await create_order(...)
    await process_payment(...)
    # If payment fails, order creation rolls back
```

### Cache Invalidation Strategy

```
When data changes:
  1. Update database
  2. Invalidate related cache keys
  3. Return updated data
  4. Async process rebuilds cache

Cache Keys:
  - products:all → Invalidate on product create/update
  - products:{id} → Invalidate on product update
  - inventory:{store_id} → Invalidate on inventory update
  - users:{id}:profile → Invalidate on user update
```

---

## Scalability

### Horizontal Scaling

**Current capacity**: 1000 concurrent users per ECS instance

**Scaling triggers**:
- CPU > 70% → Add instance
- Memory > 80% → Add instance
- Request rate > 1000 req/s → Add instance

**Example: Scale to 5,000 users**:
```
Current: 1 instance (1000 users)
Required: 5 instances (5000 users)
Auto-scaling group will add 4 instances automatically
```

### Vertical Scaling

**If single instance needs more power**:
- Increase ECS task memory: 512MB → 1GB
- Increase ECS task CPU: 256 units → 512 units
- Increase RDS instance class: db.t3.small → db.t3.large

---

## Disaster Recovery

### Recovery Time Objective (RTO): 15 minutes
### Recovery Point Objective (RPO): 5 minutes

### Backup Strategy

**Daily automated backups**:
1. RDS: Automated snapshot daily at 3 AM UTC
2. Database: Incremental WAL archival every 5 minutes
3. Application config: Version controlled in Git
4. Secrets: Encrypted in AWS Secrets Manager

### Restore Procedure

```
1. RDS Restore (5-10 minutes):
   - Create instance from latest snapshot
   - Restore to point-in-time if needed
   - Verify data integrity
   - Update DNS to new instance
   - Monitor replication lag

2. Code Restore (2-3 minutes):
   - Check out clean Git clone
   - Deploy containers
   - Run database migrations
   - Verify API health

3. Cache Restore (1-2 minutes):
   - Restart Redis cluster
   - Cache rebuilds on first request
   - Monitor cache hit rate
```

---

## Security Architecture

### Network Security

```
┌─────────────────┐
│   Internet      │
└────────┬────────┘
         │ (filtered by WAF)
         │
    ┌────▼────────────────┐
    │  AWS Security Group  │
    │  (Allow 80, 443)     │
    └────┬────────────────┘
         │
    ┌────▼─────────────────────┐
    │  Application Load         │
    │  Balancer (ALB)           │
    │  - SSL termination        │
    │  - WAF rules              │
    └────┬─────────────────────┘
         │
    ┌────▼────────────────────────┐
    │  Private Security Group      │
    │  (Allow ALB → ECS)           │
    │  (Allow ECS → RDS)           │
    │  (Allow ECS → Redis)         │
    └─────────────────────────────┘
```

### Encryption

- **In Transit**: TLS 1.3 everywhere
- **At Rest**: AES-256 (KMS keys)
- **Database**: Encrypted with customer-managed keys
- **Backups**: Same encryption as source

### Access Control

- **API Authentication**: JWT tokens
- **Database Access**: IAM role-based
- **Admin Access**: MFA required
- **SSH Access**: Disabled (use Systems Manager Session Manager)

---

## Monitoring & Observability

### Metrics Collection

**Prometheus** scrapes metrics every 15 seconds:
- API latency (per endpoint)
- Error rates (by status code)
- Database queries (by statement)
- Cache hit rates
- System metrics (CPU, memory, disk)

### Logging

**CloudWatch Logs** with structured JSON:
```json
{
  "timestamp": "2026-02-08T15:30:00Z",
  "level": "INFO",
  "service": "backend",
  "request_id": "req-123",
  "user_id": "user-456",
  "action": "product_created",
  "duration_ms": 145,
  "status": 201
}
```

### Alerting

**Severity-based escalation**:
- WARNING: Alert sent to Slack
- ERROR: Alert + SMS to on-call
- CRITICAL: Alert + SMS + page VP

---

## Cost Optimization

### Current Monthly Costs

| Component | Cost | Usage |
|-----------|------|-------|
| **Vercel** | $20 | Unlimited bandwidth |
| **RDS** | $60 | db.t3.small |
| **ElastiCache** | $30 | 3-node cluster |
| **ECS** | $100 | 3x 512MB containers |
| **NAT Gateway** | $64 | High-volume data transfer |
| **S3 Storage** | $25 | Backups |
| **CloudWatch** | $30 | Logs, metrics, alarms |
| **Total** | ~$350/month | Production-grade |

### Cost Reduction Strategies

1. Use spot instances for non-critical workloads
2. Reserve instances for 1-3 years (30-50% savings)
3. Implement caching to reduce database queries
4. Archive old logs to cheaper storage
5. Right-size instances based on actual usage

---

## Technology Decision Rationale

### Why PostgreSQL?

✅ ACID compliance for financial transactions
✅ JSON columns for flexible schema
✅ Advanced indexing (B-tree, GiST, GIN)
✅ Full-text search capabilities
✅ Replication and failover

### Why Redis?

✅ Sub-millisecond latency
✅ Cluster mode for scalability
✅ Automatic failover
✅ Multiple data structures (strings, lists, sets, hashes, sorted sets)
✅ Pub/Sub for real-time features

### Why FastAPI?

✅ Type hints with Pydantic validation
✅ Automatic OpenAPI documentation
✅ Async/await for high concurrency
✅ Built-in security features (OAuth2, API keys)
✅ Fast startup and runtime performance

### Why React + TypeScript?

✅ Component reusability
✅ Type safety catches bugs early
✅ Large ecosystem and community
✅ Virtual DOM for performance
✅ Easy state management

---

## Version & Release Management

### Semantic Versioning

- **MAJOR.MINOR.PATCH** (e.g., 1.2.3)
- MAJOR: Breaking changes
- MINOR: New features (backward compatible)
- PATCH: Bug fixes

### Release Cycle

- **Patch releases**: Weekly (if needed)
- **Minor releases**: Bi-weekly
- **Major releases**: Quarterly

### Release Checklist

- [ ] All tests passing
- [ ] Security scanning clean
- [ ] Documentation updated
- [ ] Changelog written
- [ ] Staging deployment successful
- [ ] Smoke tests passing
- [ ] Production deployment
- [ ] Monitoring verified

---

**For architectural questions**: Post in #architecture Slack channel
