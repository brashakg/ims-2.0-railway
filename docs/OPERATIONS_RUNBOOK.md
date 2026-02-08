# IMS 2.0 - Operations Runbook

## Essential Operations Guide

**Last Updated**: February 8, 2026
**Audience**: DevOps, System Administrators, Operations Team

---

## Table of Contents

1. [Daily Operations](#daily-operations)
2. [Incident Response](#incident-response)
3. [Backup & Recovery](#backup--recovery)
4. [Scaling Operations](#scaling-operations)
5. [Troubleshooting](#troubleshooting)
6. [Monitoring](#monitoring)

---

## Daily Operations

### Health Check

**Every 4 hours**, verify system health:

```bash
# 1. Check API health
curl https://api.ims-2.0.com/health

# Expected response:
# {
#   "status": "healthy",
#   "timestamp": "2026-02-08T15:30:00Z",
#   "services": {
#     "database": "healthy",
#     "cache": "healthy",
#     "api": "healthy"
#   }
# }

# 2. Check database connections
psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT count(*) FROM pg_stat_activity;"

# Expected: Should be < 80% of max_connections (typically < 80 out of 100)

# 3. Check Redis memory
redis-cli -h $REDIS_HOST -a $REDIS_PASSWORD info stats

# Expected: memory_used should be < 90% of maxmemory
```

### Log Rotation

Logs automatically rotate daily at 2 AM UTC via CloudWatch.

**Manual rotation** (if needed):
```bash
# Backend logs
docker-compose exec backend logrotate -f /etc/logrotate.d/ims-backend

# Database logs
aws logs put-retention-policy \
  --log-group-name /ims/database \
  --retention-in-days 30
```

### Performance Baseline

**Daily at 9 AM UTC**, capture baseline metrics:

```bash
#!/bin/bash

# Save current metrics to dashboard
curl -X POST http://grafana:3000/api/annotations \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Daily baseline captured",
    "tags": ["baseline", "daily"]
  }'

# Export key metrics
curl http://prometheus:9090/api/v1/query?query=up | jq . > /backups/metrics-$(date +%Y%m%d).json
```

---

## Incident Response

### Database Performance Degradation

**Symptoms**: Slow queries, high CPU, response times > 1s

**Response**:

1. **Assess severity** (1-5 minutes)
   ```bash
   # Check current load
   aws cloudwatch get-metric-statistics \
     --namespace AWS/RDS \
     --metric-name CPUUtilization \
     --dimensions Name=DBInstanceIdentifier,Value=ims-postgres-db \
     --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 60 \
     --statistics Average
   ```

2. **Identify slow queries** (5-10 minutes)
   ```sql
   -- Connect to database
   SELECT query, calls, total_time, mean_time
   FROM pg_stat_statements
   WHERE mean_time > 100  -- > 100ms average
   ORDER BY total_time DESC
   LIMIT 10;
   ```

3. **Mitigation** (5-15 minutes)
   - Option A: Kill slow query
     ```sql
     SELECT pg_terminate_backend(pid)
     FROM pg_stat_activity
     WHERE query LIKE '%<slow query pattern>%';
     ```

   - Option B: Scale up RDS instance
     ```bash
     aws rds modify-db-instance \
       --db-instance-identifier ims-postgres-db \
       --db-instance-class db.t3.small \
       --apply-immediately
     ```

   - Option C: Failover to replica (if available)
     ```bash
     aws rds failover-db-cluster \
       --db-cluster-identifier ims-postgres-cluster
     ```

4. **Post-incident** (after incident resolved)
   - Document root cause
   - Create Jira ticket for permanent fix
   - Update monitoring thresholds if needed

### Memory Leak / OOM Killer

**Symptoms**: API response times increase, services crash

**Response**:

1. **Immediate action** (< 1 minute)
   ```bash
   # Restart affected service
   docker-compose restart backend

   # Check memory usage
   free -h
   docker stats
   ```

2. **Identify memory hog** (5 minutes)
   ```bash
   # Python memory profiler
   docker-compose exec backend pip install memory_profiler

   # Run profiler on startup
   python -m memory_profiler app.py
   ```

3. **Temporary mitigation** (10 minutes)
   ```bash
   # Increase swap space
   sudo fallocate -l 4G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

4. **Long-term fix**
   - Identify memory leak in code
   - Implement caching limits
   - Profile production heap dumps

### High Error Rate (> 1%)

**Symptoms**: Alerts firing for 5XX errors

**Response**:

1. **Check error logs** (2 minutes)
   ```bash
   # CloudWatch Logs Insights
   aws logs start-query \
     --log-group-name /ims/application \
     --start-time $(date +%s -d '10 minutes ago') \
     --end-time $(date +%s) \
     --query-string 'fields @timestamp, @message | filter @message like /ERROR/ | stats count() by @message'
   ```

2. **Identify error pattern** (5 minutes)
   ```bash
   # Check if specific endpoint is affected
   curl -v https://api.ims-2.0.com/api/v1/health

   # Check recent deployments
   git log --oneline -5
   ```

3. **Rollback if needed** (5 minutes)
   ```bash
   # Revert to last stable build
   git revert HEAD
   git push origin main
   # GitHub Actions auto-deploys previous version
   ```

4. **Root cause analysis**
   - Review recent code changes
   - Check third-party service status
   - Verify database connectivity

---

## Backup & Recovery

### Daily Backup Verification

**Automated daily at 3 AM UTC**. Manually verify:

```bash
# List RDS snapshots
aws rds describe-db-snapshots \
  --db-instance-identifier ims-postgres-db \
  --query 'DBSnapshots[*].[DBSnapshotIdentifier,SnapshotCreateTime,Status]'

# Check backup size
aws s3 ls s3://ims-backups/ --recursive --human-readable --summarize

# Verify backup integrity (monthly)
aws rds modify-db-instance \
  --db-instance-identifier ims-postgres-test \
  --db-snapshot-identifier <snapshot-id> \
  --publicly-accessible false
```

### Point-in-Time Recovery

If data corruption detected:

```bash
# 1. Create recovery instance from snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier ims-postgres-recovered \
  --db-snapshot-identifier <snapshot-id>

# 2. Wait for recovery (5-15 minutes)
aws rds wait db-instance-available \
  --db-instance-identifier ims-postgres-recovered

# 3. Verify data
psql -h $RECOVERED_HOST -U $DB_USER -d $DB_NAME -c "SELECT COUNT(*) FROM customers;"

# 4. Promote to primary (if needed)
# Stop traffic to old instance
# Update connection string to new instance
# Update DNS/load balancer
```

### Redis Cache Flush

If cache corrupted:

```bash
# 1. Gracefully flush cache (minimal impact)
redis-cli -h $REDIS_HOST -a $REDIS_PASSWORD FLUSHDB ASYNC

# 2. Monitor app behavior
# - Check if cache rebuilds naturally
# - Monitor cache hit rate
# - Watch API response times

# 3. Alternative: Restart Redis container
docker-compose restart redis
# Cache will rebuild on first request to each key
```

---

## Scaling Operations

### Horizontal Scaling (Adding Servers)

```bash
# 1. Update ECS task count
aws ecs update-service \
  --cluster ims-production \
  --service ims-backend \
  --desired-count 5

# 2. Monitor scale-up progress
aws ecs describe-services \
  --cluster ims-production \
  --services ims-backend \
  --query 'services[0].[runningCount, desiredCount]'

# 3. Verify load balancing
curl -v https://api.ims-2.0.com/api/v1/health
# Should distribute across multiple instances
```

### Vertical Scaling (Bigger Machines)

```bash
# 1. Identify bottleneck
# - CPU: Increase vCPU
# - Memory: Increase RAM
# - Disk: Add storage

# 2. Update RDS instance (with downtime)
aws rds modify-db-instance \
  --db-instance-identifier ims-postgres-db \
  --db-instance-class db.t3.medium \
  --apply-immediately  # For production, schedule maintenance window

# 3. Monitor performance
watch -n 5 'aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=ims-postgres-db \
  --start-time $(date -u -d 5min ago +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average'
```

### Cache Cluster Upgrade

```bash
# 1. Create new cluster with desired config
aws elasticache create-cache-cluster \
  --cache-cluster-id ims-redis-v2 \
  --cache-node-type cache.t3.small \
  --num-cache-nodes 3

# 2. Warm new cluster
redis-cli -h $OLD_REDIS BGSAVE
redis-cli -h $NEW_REDIS < /tmp/redis-dump.rdb

# 3. Switch traffic (minimal downtime)
# Update connection string in app
# Redeploy application
```

---

## Troubleshooting

### API Timeout Issues

**Problem**: Requests to API timeout after 30 seconds

**Solution**:

1. Check if database is slow:
   ```sql
   SELECT * FROM pg_stat_statements
   ORDER BY mean_time DESC LIMIT 5;
   ```

2. Check network connectivity:
   ```bash
   telnet $API_HOST 8000
   ping $DB_HOST
   ```

3. Increase timeout (temporary):
   ```python
   # In api.py
   TIMEOUT = 60  # seconds
   ```

4. Optimize queries (permanent):
   - Add indexes on frequently filtered columns
   - Use query caching
   - Implement pagination

### High Disk Usage

**Problem**: Disk usage > 85%

**Solution**:

```bash
# 1. Identify large directories
du -sh /* | sort -rh | head -10

# 2. Clean up old logs
find /var/log -type f -name "*.log" -mtime +30 -delete

# 3. Compress old backups
tar -czf /backups/logs-$(date +%Y%m%d).tar.gz /var/log/ims/
rm -rf /var/log/ims/*

# 4. Archive to S3
aws s3 sync /backups/ s3://ims-backups/

# 5. Monitor
df -h
```

### Certificate Expiration

**Problem**: SSL certificate expires in 30 days

**Solution**:

```bash
# 1. Check certificate expiry
openssl s_client -connect api.ims-2.0.com:443 </dev/null | grep -A 2 "Validity"

# 2. Request new certificate (AWS Certificate Manager auto-renews)
# No action needed - ACM auto-renews certificates

# 3. If manual renewal needed
certbot renew --dry-run
certbot renew
systemctl reload nginx
```

---

## Monitoring

### Key Metrics to Track

**Every hour**, check these dashboards:

1. **API Performance**
   - Request rate: Should be steady
   - Error rate: Should be < 0.1%
   - Response time P95: Should be < 500ms

2. **Database Health**
   - CPU: < 80%
   - Connections: < 80% of max
   - Disk space: > 20% free
   - Replication lag: < 10s

3. **Cache Health**
   - Hit rate: > 80%
   - Memory: < 90% used
   - Evictions: = 0
   - Latency: < 1ms

### Alert Escalation Matrix

| Severity | Response Time | Notification | Escalation |
|----------|---|---|---|
| **INFO** | None | Email | None |
| **WARNING** | 15 min | Slack + Email | None |
| **ERROR** | 5 min | Slack + Email + SMS | Page on-call |
| **CRITICAL** | 1 min | Slack + Email + SMS + Phone | Page VP |

### On-Call Rotation

- **Primary**: Mon-Fri 9-5 UTC
- **Secondary**: Mon-Fri 5-9 UTC + weekends
- Handoff: Every 1 week

**On-call responsibilities**:
- Monitor Slack alerts
- Respond to incidents
- Document incidents in Jira
- Perform post-mortems

---

## Contact Information

| Role | Name | Email | Phone |
|------|------|-------|-------|
| **DevOps Lead** | John Doe | john@ims-2.0.com | +1-555-0100 |
| **Database Admin** | Jane Smith | jane@ims-2.0.com | +1-555-0101 |
| **Security Officer** | Bob Johnson | bob@ims-2.0.com | +1-555-0102 |
| **VP Engineering** | Alice Brown | alice@ims-2.0.com | +1-555-0103 |

---

**For questions or updates**: Post in #operations Slack channel
