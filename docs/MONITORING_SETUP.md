# IMS 2.0 - Monitoring & Analytics Setup

## Phase 6: Monitoring & Analytics - Complete Implementation

This document covers the complete monitoring, alerting, and analytics infrastructure for IMS 2.0, including CloudWatch dashboards, Prometheus, Grafana, and custom metrics.

---

## 1. Monitoring Architecture

### Components Overview

```
┌─────────────────────────────────────────────────────┐
│                   Applications                       │
│  (Backend API, Frontend, Database, Cache)            │
└─────────────────┬───────────────────────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
   ┌────▼────┐        ┌────▼──────┐
   │ CloudWatch       │ Prometheus │
   │ (AWS Native)     │ (Custom)   │
   └────┬────┘        └────┬──────┘
        │                  │
        └────────┬─────────┘
                 │
         ┌───────▼────────┐
         │ Grafana        │
         │ (Visualization)│
         └────────┬───────┘
                  │
      ┌───────────┴────────────┐
      │                        │
  ┌───▼────┐            ┌─────▼──┐
  │ Alerts │            │Analytics│
  │ (SNS)  │            │ Tools   │
  └────────┘            └─────────┘
```

---

## 2. CloudWatch Monitoring

### Monitored Metrics

#### Database (RDS)
- CPU Utilization (alert: > 80%)
- Database Connections (alert: > 80 connections)
- Read/Write Latency (alert: > 10ms)
- Free Storage Space (alert: < 5GB)
- Replication Lag (alert: > 10s)
- IOPS Used

#### Cache (Redis)
- CPU Utilization (alert: > 75%)
- Memory Usage (alert: > 90%)
- Evictions (alert: > 0)
- Cache Hit Rate (alert: < 80%)
- Network Bytes In/Out

#### Application
- 5XX Error Count (alert: > 50/5min)
- Response Time (alert: > 2s average)
- Request Count
- Successful Requests
- Failed Requests

### CloudWatch Alarms

**Critical Alerts** (SNS topic: `ims-critical-alerts`)
- RDS storage below 5GB
- Redis memory above 90%
- API error rate above 10%
- Unauthorized access spike
- Key evictions from Redis

**Warning Alerts** (SNS topic: `ims-alerts`)
- RDS CPU above 80%
- API response time above 2s
- Application errors above 10/5min
- Database connections above 80
- Cache hit rate below 80%

### Dashboard Access

CloudWatch native dashboard at:
```
https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=ims-main-dashboard
```

---

## 3. Prometheus Setup

### Configuration File: `prometheus.yml`

**Scrape Targets**:
1. **Prometheus** (self-monitoring)
   - Port: 9090
   - Interval: 15s

2. **Backend API**
   - Port: 8000/metrics
   - Interval: 30s
   - Path: `/metrics`

3. **PostgreSQL Exporter**
   - Port: 9187
   - Interval: 15s

4. **Redis Exporter**
   - Port: 9121
   - Interval: 15s

5. **Node Exporter** (system metrics)
   - Port: 9100
   - Interval: 15s

6. **Blackbox Exporter** (endpoint availability)
   - Port: 9115
   - Monitors API health endpoints

### Prometheus Storage

**Data Retention**: 15 days
**Scrape Interval**: 15 seconds
**Query Resolution**: 1 minute
**Storage Size**: ~2GB per week (adjustable)

### Key Queries

```promql
# API error rate
rate(http_requests_total{status=~"5.."}[5m])

# P95 response time
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Database CPU
pg_stat_cpu_utilization / 100

# Cache hit rate
rate(redis_hits_total[5m]) / (rate(redis_hits_total[5m]) + rate(redis_misses_total[5m]))

# Memory usage
(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100
```

---

## 4. Alert Rules

### Alert Categories

#### Application Alerts (`application_alerts`)
- **HighAPIResponseTime**: P95 response time > 1s for 5 minutes
- **HighErrorRate**: 5XX error rate > 5% for 2 minutes
- **RequestRateSpike**: Traffic 2x above normal average

#### Database Alerts (`database_alerts`)
- **PostgreSQLHighCPU**: CPU utilization > 80%
- **PostgreSQLConnectionPoolExhaustion**: Connections > 90% of max
- **PostgreSQLLongTransaction**: Transaction running > 600s
- **PostgreSQLReplicationLag**: Replication lag > 10s

#### Cache Alerts (`cache_alerts`)
- **LowCacheHitRate**: Hit rate < 80%
- **RedisMemoryHigh**: Memory usage > 90%
- **RedisEvictions**: Key evictions detected
- **RedisConnectionRejected**: Connections being rejected

#### Infrastructure Alerts (`infrastructure_alerts`)
- **HighMemoryUsage**: Memory > 85%
- **HighDiskUsage**: Disk > 85%
- **LowDiskSpace**: Available space < 5GB

#### Security Alerts (`security_alerts`)
- **BruteForceAttempts**: Failed logins > 10/sec
- **SQLInjectionAttempts**: SQL injection pattern detected
- **XSSAttempts**: XSS pattern detected
- **UnusualTrafficPattern**: Traffic 3x above 24h average

#### Business Alerts (`business_alerts`)
- **LowTransactionSuccessRate**: Success rate < 95%
- **ZeroSalesDetected**: No sales in 1 hour

### Alert Configuration

Alerts are fired based on:
1. Threshold exceeded
2. Duration threshold (e.g., 5 minutes)
3. Label matching (component, severity)

**Notification Workflow**:
```
Prometheus → Alertmanager → Email/Slack/PagerDuty
```

---

## 5. Grafana Dashboards

### Available Dashboards

#### 1. System Overview
- API Request Rate
- API Error Rate
- API Response Time (P95)
- Database Connections
- Database CPU
- Redis Hit Rate
- Redis Memory Usage
- System Memory
- Disk Usage

**Update Frequency**: 30 seconds
**Time Range**: Last 1 hour (configurable)

#### 2. API Performance
- Request Rate by Endpoint
- Response Time by Endpoint (P95)
- Error Rate by Status Code
- Request Size Distribution

**Focus**: API-level metrics and SLO tracking

#### 3. Database Health
- Query Execution Time
- Active Connections
- Transaction Rate
- Index Efficiency
- Database Size
- Replication Lag
- Backup Status

**Focus**: Database performance and reliability

### Accessing Grafana

```
URL: http://localhost:3000
Default Username: admin
Default Password: (set during setup)
```

### Creating Custom Dashboards

1. Click "+" → "Dashboard"
2. Click "Add Panel"
3. Select Prometheus as datasource
4. Write PromQL query
5. Choose visualization type
6. Save dashboard

---

## 6. Logging & Log Aggregation

### Log Groups

| Log Group | Retention | Purpose |
|-----------|-----------|---------|
| `/ims/application` | 30 days | App logs, errors, info |
| `/ims/database` | 30 days | PostgreSQL logs |
| `/ims/api-gateway` | 7 days | API Gateway access logs |
| `/ims/security` | 90 days | Auth, access control, suspicious activity |

### Log Analysis

**CloudWatch Insights Queries**:

```sql
-- Find top error sources
fields @timestamp, @message, @logStream
| stats count() as error_count by @logStream
| sort error_count desc

-- Slow query analysis
fields @timestamp, query_time
| filter query_time > 1000
| stats avg(query_time), max(query_time) by query_category

-- Failed login attempts
fields @timestamp, user_id, ip_address
| filter event = "login_failed"
| stats count() as failed_attempts by user_id, ip_address

-- API latency by endpoint
fields @timestamp, endpoint, response_time
| stats pct(response_time, 95) as p95, pct(response_time, 99) as p99 by endpoint
```

### Exporting Logs

```bash
# Export to S3 for long-term archival
aws logs create-export-task \
  --log-group-name /ims/application \
  --from 1610000000000 \
  --to 1610100000000 \
  --destination my-bucket \
  --destination-prefix logs/
```

---

## 7. Custom Metrics

### Application Metrics

**Import in Python**:
```python
from prometheus_client import Counter, Histogram, Gauge

# Request counter
http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

# Response time histogram
http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency',
    ['endpoint'],
    buckets=(0.1, 0.5, 1, 2, 5, 10)
)

# Active database connections
db_connections = Gauge(
    'db_connections_active',
    'Active database connections'
)
```

**Usage**:
```python
# In request handler
start_time = time.time()
try:
    # Process request
    http_requests_total.labels(
        method='GET',
        endpoint='/api/products',
        status=200
    ).inc()
finally:
    duration = time.time() - start_time
    http_request_duration_seconds.labels(
        endpoint='/api/products'
    ).observe(duration)
```

### Metrics to Expose

**API Metrics**:
- `http_requests_total` - Total requests by method/endpoint/status
- `http_request_duration_seconds` - Request latency histogram
- `http_request_size_bytes` - Request payload size
- `http_response_size_bytes` - Response payload size

**Business Metrics**:
- `transactions_total` - Total transactions
- `transactions_completed_total` - Completed transactions
- `sales_total` - Total sales amount
- `order_processing_time_seconds` - Order fulfillment time

**Database Metrics**:
- `db_query_duration_seconds` - Query execution time
- `db_connections_active` - Active connections
- `db_transactions_total` - Transaction count

---

## 8. Performance Targets & SLOs

### Service Level Objectives (SLOs)

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Availability | 99.9% | < 99.8% |
| API Response Time (P95) | < 500ms | > 1s |
| API Response Time (P99) | < 1s | > 2s |
| Error Rate | < 0.1% | > 0.5% |
| Cache Hit Rate | > 85% | < 80% |
| Database Latency (P95) | < 10ms | > 20ms |

### Error Budget

Monthly error budget for 99.9% SLO:
```
(1 - 0.999) × 43,200 minutes = 43.2 minutes

Means: System can be down 43 minutes/month
and still meet SLO
```

---

## 9. Analytics Integration

### User Analytics

**Track**:
- Daily Active Users (DAU)
- Monthly Active Users (MAU)
- Session duration
- Feature usage
- User retention rate
- Conversion funnel

**Implementation**:
```python
# Track user event
analytics.track_event(
    user_id="user-123",
    event_name="order_completed",
    properties={
        "order_value": 5000,
        "items_count": 3,
        "payment_method": "card"
    }
)
```

### Business Analytics

**Sales Metrics**:
- Daily sales revenue
- Order conversion rate
- Average order value
- Customer acquisition cost
- Customer lifetime value

**Operational Metrics**:
- Order fulfillment time
- Return rate
- Customer satisfaction score
- API usage per tenant

---

## 10. Setting Up Monitoring Stack

### Prerequisites

- Docker & Docker Compose
- Prometheus
- Grafana
- PostgreSQL Exporter
- Redis Exporter
- Node Exporter
- Alertmanager

### Quick Start

1. **Pull monitoring images**:
```bash
docker pull prom/prometheus
docker pull grafana/grafana
docker pull prometheuscommunity/postgres-exporter
docker pull oliver006/redis_exporter
docker pull prom/node-exporter
docker pull prom/alertmanager
```

2. **Start monitoring stack**:
```bash
docker-compose -f docker-compose.monitoring.yml up -d
```

3. **Configure Prometheus**:
```bash
cp monitoring/prometheus.yml /etc/prometheus/
docker-compose exec prometheus kill -HUP 1
```

4. **Add Grafana datasource**:
   - URL: http://prometheus:9090
   - Type: Prometheus
   - Save & Test

5. **Import dashboards**:
   - Upload `monitoring/grafana-dashboards.json`
   - Select default datasource

6. **Configure alerts**:
```bash
cp monitoring/alert_rules.yml /etc/prometheus/rules/
docker-compose exec prometheus kill -HUP 1
```

---

## 11. Troubleshooting

### Prometheus Issues

**Prometheus won't start**:
```bash
# Validate config
promtool check config /etc/prometheus/prometheus.yml

# Check logs
docker-compose logs prometheus
```

**No metrics being scraped**:
```bash
# Check targets
curl http://localhost:9090/api/v1/targets

# Verify scrape config
curl http://localhost:9090/api/v1/query?query=up
```

### Grafana Issues

**Datasource connection failed**:
```bash
# Verify Prometheus is running
curl http://prometheus:9090/-/healthy

# Check Grafana logs
docker-compose logs grafana
```

**Dashboards not loading**:
- Clear browser cache
- Verify datasource is set to default
- Check Prometheus has data for queries

---

## 12. Best Practices

✅ **Alerting**
- Set meaningful alert thresholds (avoid false positives)
- Use runbooks for escalation
- Test alert workflows monthly
- Document alert meanings

✅ **Dashboards**
- Focus on critical metrics
- Use consistent color schemes
- Update regularly based on feedback
- Version control dashboard definitions

✅ **Logging**
- Include context (user ID, request ID, trace ID)
- Use structured logging (JSON)
- Set appropriate retention periods
- Exclude sensitive data

✅ **Metrics**
- Use consistent naming conventions
- Include labels for segmentation
- Keep cardinality under control
- Document metric meanings

---

## 13. Cost Optimization

### CloudWatch Costs
- **Metrics**: $0.30 per metric per month
- **Logs**: $0.50 per GB ingested
- **Alarms**: $0.10 per alarm per month

**For IMS**: ~$500-1000/month

### Prometheus Storage
- **Local Storage**: Self-hosted, minimal cost
- **Remote Storage**: ~$200/month (S3 + Thanos)
- **Data Retention**: 15 days (adjustable)

### Cost Reduction Tips
1. Reduce metric cardinality
2. Adjust scrape intervals (15s → 30s)
3. Delete unused metrics
4. Archive old logs to S3
5. Use sampling for high-volume events

---

## 14. Next Steps (Phase 7)

After Phase 6 is complete:

**Phase 7: Security Hardening** (6 weeks)
- Two-factor authentication
- Advanced RBAC
- Encryption at rest
- Audit logging
- Penetration testing

---

## Resources

- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [CloudWatch Documentation](https://docs.aws.amazon.com/cloudwatch/)
- [SRE Book - Monitoring](https://sre.google/sre-book/monitoring-distributed-systems/)

---

**Last Updated**: February 8, 2026
**Phase Status**: ✅ **COMPLETE**
**Monitoring Infrastructure**: Production Ready
**Dashboards**: 3 pre-configured
**Alert Rules**: 20+ rules
**Data Retention**: 15 days (Prometheus), 30 days (CloudWatch)
