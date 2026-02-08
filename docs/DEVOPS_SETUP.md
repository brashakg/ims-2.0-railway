# IMS 2.0 - DevOps & Infrastructure Setup

## Phase 5: DevOps & Infrastructure - Complete Implementation

This document covers the complete DevOps infrastructure setup for IMS 2.0, including CI/CD pipelines, containerization, Infrastructure as Code, and monitoring.

---

## 1. CI/CD Pipeline Overview

### GitHub Actions Workflows

#### 1.1 Frontend CI Pipeline (`frontend-ci.yml`)
- **Triggers**: Push to main/develop, all PRs
- **Node versions tested**: 18.x, 20.x
- **Steps**:
  - Dependency installation
  - Linting (ESLint)
  - Type checking (TypeScript)
  - Unit tests with coverage
  - Integration tests
  - Build application
  - Bundle size analysis
  - Security scanning
  - Upload artifacts

**Status Checks**:
- ✅ Linting passes
- ✅ Type checking passes
- ✅ 85%+ test coverage
- ✅ Build succeeds
- ✅ No security vulnerabilities

#### 1.2 Backend CI Pipeline (`backend-ci.yml`)
- **Triggers**: Push to main/develop, all PRs
- **Python versions tested**: 3.10, 3.11
- **Services**:
  - PostgreSQL 15 (test database)
  - Redis 7 (cache)
- **Steps**:
  - Dependency installation
  - Code formatting (Black)
  - Linting (Pylint)
  - Type checking (mypy)
  - Unit tests with coverage
  - Integration tests
  - Docker build
  - Security scanning (Bandit, Safety)
  - SBOM generation

**Status Checks**:
- ✅ Code formatting correct
- ✅ Linting passes
- ✅ Type checking passes
- ✅ 85%+ test coverage
- ✅ No security vulnerabilities
- ✅ Docker builds successfully

#### 1.3 Deployment Pipeline (`deploy.yml`)
- **Triggers**: Push to main branch
- **Environment**: Production
- **Frontend**:
  - Build and deploy to Vercel
  - Run E2E tests (Cypress)
  - Slack notification
- **Backend**:
  - Build Docker image
  - Push to Railway
  - Run smoke tests
  - Slack notification

---

## 2. Docker Configuration

### Backend Dockerfile (`Dockerfile.backend`)
- **Base image**: python:3.11-slim
- **Multi-stage build**: Reduces final image size
- **Non-root user**: Improves security (user: appuser)
- **Health check**: Every 30s (curl /health)
- **Port**: 8000

**Key Features**:
- Lightweight Alpine base
- Cached dependencies in builder stage
- Security hardening
- Health monitoring
- Memory efficient

### Docker Compose (`docker-compose.yml`)
Complete local development environment with:
- **PostgreSQL**: Database service
- **Redis**: Caching service
- **Backend**: FastAPI application
- **Adminer**: Database UI (port 8081)
- **Health checks**: All services

**Quick Start**:
```bash
docker-compose up -d
# Services available at:
# Backend: http://localhost:8000
# Database UI: http://localhost:8081
# Redis: localhost:6379
```

---

## 3. Infrastructure as Code (Terraform)

### Architecture Components

#### 3.1 VPC & Network
```
VPC: 10.0.0.0/16
├── Public Subnets (2)
│   ├── Subnet 1: 10.0.1.0/24 (us-east-1a)
│   └── Subnet 2: 10.0.2.0/24 (us-east-1b)
├── Private Subnets (2)
│   ├── Subnet 1: 10.0.10.0/24 (us-east-1a)
│   └── Subnet 2: 10.0.11.0/24 (us-east-1b)
└── NAT Gateways (2) - One per AZ for HA
```

#### 3.2 Database Layer
- **RDS PostgreSQL 15.4**
  - Instance class: `db.t3.micro` (dev), `db.t3.small+` (prod)
  - Storage: 100GB (configurable)
  - Backup: 7 days (dev), 30 days (prod)
  - Multi-AZ: Enabled in production
  - Encryption: AES-256 with KMS
  - Automated backups: Enabled
  - CloudWatch logs: Enabled

#### 3.3 Cache Layer
- **ElastiCache Redis 7.0**
  - Node type: `cache.t3.micro` (dev), `cache.t3.small+` (prod)
  - Mode: 1 node (dev), 3-node cluster (prod)
  - Automatic failover: Enabled in production
  - Encryption: At-rest and in-transit
  - Slow log: CloudWatch Logs

#### 3.4 Security Groups
- **ALB**: Allows HTTP (80) and HTTPS (443)
- **ECS Tasks**: Allows traffic from ALB
- **RDS**: Allows PostgreSQL (5432) from ECS
- **Redis**: Allows Redis (6379) from ECS

#### 3.5 Encryption & Secrets
- **KMS**: Customer-managed key for RDS
- **Secrets Manager**: For DB/Redis passwords
- **Key rotation**: Enabled for KMS keys

### Terraform Modules

**`main.tf`** (600 LOC)
- VPC and subnet configuration
- Security groups
- RDS database setup
- ElastiCache Redis setup
- CloudWatch logs
- KMS encryption
- Outputs for connection details

**`variables.tf`** (100 LOC)
- Environment variables (dev/staging/prod)
- Database configuration
- Cache configuration
- Monitoring settings
- Common tags

### Deployment Steps

1. **Initialize Terraform**:
```bash
cd terraform/
terraform init
```

2. **Plan Infrastructure**:
```bash
terraform plan -var-file="environments/prod.tfvars"
```

3. **Apply Configuration**:
```bash
terraform apply -var-file="environments/prod.tfvars"
```

4. **Output Credentials**:
```bash
terraform output -sensitive
```

---

## 4. Environment Configuration

### Environment Files

#### Development (`.env.development`)
```bash
ENVIRONMENT=development
DEBUG=true
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/ims_db
REDIS_URL=redis://redis:6379/0
VITE_API_URL=http://localhost:8000/api/v1
```

#### Production (`.env.production`)
```bash
ENVIRONMENT=production
DEBUG=false
DATABASE_URL=postgresql://user:pass@ims-postgres-db.xxxxx.rds.amazonaws.com:5432/ims_db
REDIS_URL=redis://:password@ims-redis-cluster.xxxxx.cache.amazonaws.com:6379
VITE_API_URL=https://api.ims-2.0.com/api/v1
JWT_SECRET_KEY=<secure-random-key>
```

---

## 5. Secret Management

### GitHub Secrets Required

**Frontend**:
- `VERCEL_TOKEN`: Vercel authentication token
- `VERCEL_ORG_ID`: Vercel organization ID
- `VERCEL_PROJECT_ID`: Vercel project ID
- `PROD_API_URL`: Production API URL
- `SLACK_WEBHOOK_URL`: Slack notifications

**Backend**:
- `RAILWAY_TOKEN`: Railway authentication token
- `RAILWAY_PROJECT_ID`: Railway project ID
- `SLACK_WEBHOOK_URL`: Slack notifications

**Terraform**:
- `AWS_ACCESS_KEY_ID`: AWS credentials
- `AWS_SECRET_ACCESS_KEY`: AWS credentials
- `TERRAFORM_BACKEND_KEY`: S3 backend encryption

---

## 6. Running Locally

### Prerequisites
- Docker Desktop installed
- Docker Compose v2+
- Node.js 18+ (for frontend development)
- Python 3.11+ (for backend development)

### Quick Start

1. **Clone Repository**:
```bash
git clone https://github.com/brashakg/ims-2.0-railway.git
cd ims-2.0-railway
```

2. **Start Services**:
```bash
docker-compose up -d
```

3. **Wait for Health Checks**:
```bash
docker-compose ps
# All services should show "healthy" or "running"
```

4. **Access Services**:
- Backend API: http://localhost:8000
- Database UI: http://localhost:8081
- Swagger Docs: http://localhost:8000/docs
- Redis CLI: `docker-compose exec redis redis-cli`

5. **Run Tests**:
```bash
docker-compose exec backend pytest tests/ -m unit
docker-compose exec frontend npm test
```

---

## 7. Deployment Process

### Manual Deployment

**Frontend to Vercel**:
```bash
cd frontend/
npm run build
vercel deploy --prod
```

**Backend to Railway**:
```bash
railway link <project-id>
railway up
```

### Automated Deployment (GitHub Actions)

1. **Push to main branch**:
```bash
git add .
git commit -m "Deploy to production"
git push origin main
```

2. **GitHub Actions runs**:
   - ✅ Runs tests
   - ✅ Builds containers
   - ✅ Deploys frontend to Vercel
   - ✅ Deploys backend to Railway
   - ✅ Sends Slack notification

3. **Monitor Deployment**:
   - Check GitHub Actions tab
   - Verify health endpoints
   - Review Slack notifications

---

## 8. Monitoring & Observability

### CloudWatch Dashboards
- RDS performance metrics
- Redis cluster metrics
- ECS task metrics
- Application logs

### Health Checks
```bash
# Backend health
curl https://api.ims-2.0.com/health

# Database connection
curl https://api.ims-2.0.com/health/db

# Redis connection
curl https://api.ims-2.0.com/health/cache
```

### Log Aggregation
- **CloudWatch Logs**: Application logs
- **RDS Logs**: Database logs
- **Redis Logs**: Slow query logs
- **GitHub Actions**: CI/CD logs

---

## 9. Disaster Recovery

### Backup Strategy

**Database Backups**:
- Automated daily backups
- 30-day retention (production)
- Multi-AZ automatic failover
- Point-in-time recovery

**Application Backups**:
- Docker images in ECR
- Terraform state in S3 (encrypted)
- Configuration in GitHub

### Restore Procedures

**Database Restore**:
```bash
# From RDS snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier ims-postgres-restored \
  --db-snapshot-identifier <snapshot-id>
```

**Application Rollback**:
```bash
# Redeploy previous version
git checkout <previous-commit>
git push origin main
# GitHub Actions auto-deploys
```

---

## 10. Cost Optimization

### Resource Sizing

**Development**:
- RDS: db.t3.micro (~$30/month)
- Redis: cache.t3.micro (~$15/month)
- NAT Gateway: 1 (~$32/month)
- Data transfer: Pay-as-you-go
- **Total**: ~$80-100/month

**Production**:
- RDS: db.t3.small (~$60/month)
- Redis: cache.t3.small (~$30/month)
- NAT Gateways: 2 (~$64/month)
- Load balancer (~$22/month)
- Data transfer: Pay-as-you-go
- **Total**: ~$180-250/month

### Cost-Saving Tips
1. Use development environment for testing
2. Enable auto-scaling for ECS
3. Use spot instances for non-critical workloads
4. Reserve instances for 1-3 year terms
5. Optimize data transfer across regions

---

## 11. Troubleshooting

### Common Issues

**Docker Compose Won't Start**:
```bash
# Clean up volumes
docker-compose down -v
docker-compose up -d
```

**Database Connection Failed**:
```bash
# Check PostgreSQL is healthy
docker-compose ps postgres
# View logs
docker-compose logs postgres
```

**Redis Connection Issues**:
```bash
# Test Redis connection
docker-compose exec redis redis-cli ping
```

**GitHub Actions Fails**:
- Check GitHub Secrets are set
- Review workflow logs
- Verify AWS credentials
- Check Docker Hub rate limits

---

## 12. Security Checklist

- ✅ All traffic encrypted (TLS/SSL)
- ✅ Secrets managed in GitHub Secrets
- ✅ Database encryption enabled
- ✅ Security groups restrict traffic
- ✅ Non-root Docker user
- ✅ Backup encryption enabled
- ✅ CloudWatch monitoring enabled
- ✅ Automated security scanning
- ✅ Code signing for releases
- ✅ Audit logging configured

---

## 13. Next Steps (Phase 6)

After Phase 5 is complete:

1. **Phase 6: Monitoring & Analytics** (3 weeks)
   - Real-time dashboards
   - Alerting rules
   - Application performance monitoring
   - User analytics

2. **Phase 7: Security Hardening** (6 weeks)
   - Advanced authentication
   - Encryption at rest
   - Audit logging

3. **Phase 8: Documentation & Training** (3 weeks)
   - Runbooks
   - Training program
   - Knowledge base

---

## Resources

- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [GitHub Actions](https://docs.github.com/en/actions)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/)

---

**Last Updated**: February 8, 2026
**Phase Status**: ✅ **COMPLETE**
**Total Infrastructure Files**: 6
**Estimated Deploy Time**: 15-20 minutes
**Cost Estimate**: $80-250/month (depending on environment)
