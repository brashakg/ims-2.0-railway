# IMS 2.0 - Retail Operating System

**Complete, Production-Ready Deployment Package**

---

## 📋 Overview

IMS 2.0 is a comprehensive **Retail Operating System** for optical and lifestyle retail businesses.
This is NOT just a POS - it's a complete retail governance system with full-stack implementation.

**Core Philosophy**: Control > Convenience | Explicit > Implicit | Audit Everything

### What's Included

✅ **Complete Full-Stack Application**
- Frontend: React 19 + TypeScript + Vite + Tailwind CSS
- Backend: FastAPI + Python
- Database: MongoDB with complete schema
- All deployment files and scripts

✅ **Production-Ready Infrastructure**
- Docker & Docker Compose configuration
- Nginx reverse proxy with SSL support
- Database initialization & migration scripts
- Automated backup & restore scripts
- Health checks & monitoring

✅ **Comprehensive Documentation**
- Quick start guide (`QUICKSTART.md`)
- Full deployment guide (`DEPLOYMENT.md`)
- Project handover summary (`IMS_2.0_HANDOVER_SUMMARY.md`)

---

## 🚀 Quick Start

**Get up and running in 5 minutes:**

```bash
# 1. Setup
./scripts/setup.sh

# 2. Configure (edit .env file)
nano .env

# 3. Deploy
./scripts/deploy.sh

# 4. Access
# Frontend: http://localhost
# Backend: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

**Default Login:**
- Username: `admin`
- Password: `admin123`

⚠️ **CHANGE PASSWORD IMMEDIATELY AFTER FIRST LOGIN!**

📖 **See `QUICKSTART.md` for detailed setup instructions**

---

## 🏗️ Architecture

### Backend Modules (Complete)

| Module | File | Lines | Features |
|--------|------|-------|----------|
| Main App | ims_app.py | 303 | Unified system, workflows |
| Inventory | inventory_engine.py | 1,255 | Stock, transfers, alerts |
| POS | pos_engine.py | 933 | Sales, orders, payments |
| Pricing | pricing_engine.py | 640 | Discounts, approvals |
| Clinical | clinical_engine.py | 443 | Eye tests, prescriptions |
| Customer | customer_engine.py | 308 | CRM, patient management |
| Vendor | vendor_engine.py | 452 | Procurement, GRN |
| HR | hr_engine.py | 377 | Attendance, payroll |
| Finance | finance_engine.py | 891 | Invoices, GST, till |
| Tasks | tasks_engine.py | 1,165 | SOPs, escalations |
| Workshop | workshop_engine.py | 326 | Lens fitting, jobs |
| Reports | reports_engine.py | 547 | Analytics, insights |
| Marketplace | marketplace_engine.py | 401 | Shopify, shipping |
| Integrations | integrations_engine.py | 723 | Tally, Razorpay, WhatsApp |
| AI Intelligence | ai_intelligence_engine.py | 389 | Pattern detection (read-only) |

**Total**: ~12,700+ lines of production Python code

### Frontend (Complete)

- 38+ React components with TypeScript
- Role-based dashboards (7 types)
- POS interface with optical workflow
- Inventory management screens
- Customer & patient management
- HR & payroll interfaces
- Reports & analytics
- Settings & configuration

### Database (MongoDB)

- 19 collections with complete schemas
- 70+ optimized indexes
- 20 repository classes
- Automatic initialization
- Backup & restore scripts

---

## 📦 Features

### Core Modules

- ✅ Multi-store management (Better Vision + WizOpt)
- ✅ Product catalog (6 categories)
- ✅ Inventory tracking & transfers
- ✅ Point of Sale (POS)
- ✅ Customer & patient management
- ✅ Optical prescriptions
- ✅ Order management
- ✅ Vendor & procurement (PO, GRN)
- ✅ HR & payroll
- ✅ Task management & SOPs
- ✅ Expense tracking
- ✅ Workshop (lens fitting)
- ✅ Reports & analytics
- ✅ Role-based access control (10 roles)

### Product Categories

1. Frames & Sunglasses
2. Optical Lenses
3. Contact Lenses
4. Watches & Smartwatches
5. Accessories
6. Services

### User Roles

1. **SUPERADMIN** - CEO, full control, AI access
2. **ADMIN** - Directors, HQ level
3. **AREA_MANAGER** - Multi-store oversight
4. **STORE_MANAGER** - Store operations
5. **ACCOUNTANT** - Finance & GST
6. **CATALOG_MANAGER** - Product management
7. **OPTOMETRIST** - Eye tests
8. **SALES_STAFF** - Sales operations
9. **CASHIER** - Payment processing
10. **WORKSHOP_STAFF** - Lens fitting

### Business Rules

- **MRP < Offer Price → BLOCK** (Cannot sell)
- **Role-based discount caps** (Sales: 10%, Manager: 20%, Area Manager: 25%)
- **AI is READ-ONLY** (Superadmin-only, advisory mode)
- **Audit Everything** (Who, What, When, Where, Previous, New)

---

## 🛠️ Tech Stack

### Frontend
- React 19.2.0
- TypeScript 5.9.3
- Vite 7.2.4
- Tailwind CSS 4.1.18
- React Router 6.30.3
- React Query 5.90.19
- Axios 1.13.2

### Backend
- Python 3.12
- FastAPI 0.115.0
- Uvicorn 0.32.0
- Pydantic 2.9.0
- PyMongo 4.10.1
- PyJWT 2.9.0

### Database
- MongoDB 7.0

### Infrastructure
- Docker 24.0+
- Docker Compose 2.20+
- Nginx 1.27

---

## 📁 Project Structure

```
ims-2.0-core/
├── backend/
│   ├── api/               # FastAPI application
│   ├── core/              # Business logic engines
│   ├── database/          # MongoDB schemas & repositories
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/               # React application
│   ├── public/            # Static assets
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
├── scripts/
│   ├── setup.sh           # Initial setup
│   ├── deploy.sh          # Deploy application
│   ├── stop.sh            # Stop services
│   ├── backup.sh          # Database backup
│   ├── restore.sh         # Database restore
│   └── init-mongo.js      # MongoDB initialization
├── nginx/
│   ├── nginx.conf         # Production reverse proxy
│   └── ssl/               # SSL certificates
├── docker-compose.yml     # Service orchestration
├── .env.example           # Environment template
├── QUICKSTART.md          # 5-minute setup guide
├── DEPLOYMENT.md          # Complete deployment guide
└── README.md              # This file
```

---

## 📊 Statistics

| Component | Count |
|-----------|-------|
| Core Modules | 21 |
| Database Collections | 19 |
| Repository Classes | 20 |
| API Endpoints | 149 |
| Frontend Components | 38+ |
| User Roles | 10 |
| Total Python Lines | ~18,200 |
| Total TypeScript Lines | ~8,500 |

---

## 🚀 Deployment Options

### Docker Compose (Recommended)
```bash
./scripts/deploy.sh
```

### Cloud Platforms
- Railway.app
- Render.com
- DigitalOcean App Platform
- AWS, GCP, Azure

### VPS/Dedicated Server
- Ubuntu Server 22.04+
- Debian 12+
- Any Docker-compatible host

📖 **See `DEPLOYMENT.md` for detailed deployment instructions**

---

## 🔧 Management Commands

```bash
# View logs
docker compose logs -f

# Check status
docker compose ps

# Restart services
docker compose restart

# Backup database
./scripts/backup.sh

# Restore database
./scripts/restore.sh <backup-file>

# Stop services
./scripts/stop.sh

# Remove all data (⚠️ careful!)
./scripts/stop.sh --volumes
```

---

## 📚 Documentation

- **`QUICKSTART.md`** - Get started in 5 minutes
- **`DEPLOYMENT.md`** - Complete deployment guide
- **`IMS_2.0_HANDOVER_SUMMARY.md`** - Project overview & business rules
- **`SYSTEM_INTENT.md`** - Supreme authority document (in docs/)

---

## 🔒 Security

- JWT-based authentication
- Role-based access control (RBAC)
- Rate limiting (configurable)
- SQL injection protection
- XSS protection
- CORS configuration
- HTTPS/SSL support
- Secure password hashing (bcrypt)
- Environment variable isolation
- Audit logging

---

## 🆘 Support & Troubleshooting

### Common Issues

**Services won't start:**
```bash
docker compose logs
```

**Port conflicts:**
```env
# Edit .env
API_PORT=8001
FRONTEND_PORT=8080
```

**Database connection failed:**
```bash
docker compose restart mongodb
docker compose logs mongodb
```

📖 **See `DEPLOYMENT.md` for comprehensive troubleshooting**

---

## 📝 License

Proprietary - All Rights Reserved

---

## 📞 Contact

For support or inquiries:
- Email: support@ims2.com
- Documentation: See included guides

---

**Version:** 2.0.0
**Release Date:** 2026-01-22
**Status:** Production Ready

---

**Ready to deploy? Start with `./scripts/setup.sh`**
