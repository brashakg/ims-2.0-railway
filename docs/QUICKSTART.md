# IMS 2.0 - Quick Start Guide

Get IMS 2.0 up and running in 5 minutes!

---

## ⚡ 5-Minute Setup

### Step 1: Install Docker

**Already have Docker?** Skip to Step 2.

**Don't have Docker?** Install it:

```bash
# Linux (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in

# macOS: Download Docker Desktop
# https://www.docker.com/products/docker-desktop/

# Windows: Download Docker Desktop with WSL2
# https://docs.docker.com/desktop/windows/wsl/
```

### Step 2: Run Setup

```bash
cd ims-2.0-core
chmod +x scripts/*.sh
./scripts/setup.sh
```

### Step 3: Configure

Edit `.env` file (minimum required):

```bash
nano .env
```

**Change these:**
```env
MONGO_USERNAME=your_username
MONGO_PASSWORD=your_secure_password
JWT_SECRET_KEY=generate_with_openssl_rand_hex_32
```

**Generate JWT secret:**
```bash
openssl rand -hex 32
```

### Step 4: Deploy

```bash
./scripts/deploy.sh
```

Wait 30-60 seconds for services to start...

### Step 5: Access

Open your browser:

- **Application:** http://localhost
- **API Docs:** http://localhost:8000/docs

**Login:**
- Username: `admin`
- Password: `admin123`

⚠️ **CHANGE PASSWORD IMMEDIATELY!**

---

## 🎯 What You Get

### Complete System

- ✅ Frontend (React + TypeScript + Tailwind CSS)
- ✅ Backend API (FastAPI + Python)
- ✅ Database (MongoDB)
- ✅ All configured and ready to use

### Features Available

- Multi-store management
- Product catalog
- Inventory tracking
- Point of Sale (POS)
- Customer management
- Prescriptions (optical)
- Orders & sales
- Vendor management
- HR & payroll
- Tasks & SOPs
- Expense tracking
- Workshop management
- Reports & analytics
- Role-based access control

### User Roles

1. SUPERADMIN - Full control
2. ADMIN - Administrative access
3. AREA_MANAGER - Multi-store oversight
4. STORE_MANAGER - Store operations
5. ACCOUNTANT - Financial management
6. CATALOG_MANAGER - Product management
7. OPTOMETRIST - Eye tests
8. SALES_STAFF - Sales operations
9. CASHIER - Payment processing
10. WORKSHOP_STAFF - Lens fitting

---

## 🔧 Common Commands

**View logs:**
```bash
docker compose logs -f
```

**Stop services:**
```bash
./scripts/stop.sh
```

**Restart:**
```bash
docker compose restart
```

**Backup database:**
```bash
./scripts/backup.sh
```

**Check status:**
```bash
docker compose ps
```

---

## 🆘 Quick Troubleshooting

### Services won't start?

**Port already in use:**
```bash
# Change ports in .env
API_PORT=8001
FRONTEND_PORT=8080
```

**Permission denied:**
```bash
sudo chown -R $USER:$USER .
```

### Can't access frontend?

**Check it's running:**
```bash
docker compose ps
curl http://localhost/health
```

**Hard refresh browser:**
- Windows/Linux: `Ctrl + Shift + R`
- macOS: `Cmd + Shift + R`

### Database connection failed?

**Check MongoDB is running:**
```bash
docker compose logs mongodb
```

**Restart services:**
```bash
docker compose restart
```

---

## 📚 Next Steps

1. **Change default password** (Settings → Change Password)
2. **Configure stores** (Settings → Stores)
3. **Add users** (Settings → Users)
4. **Import products** (Products → Import)
5. **Configure roles** (Settings → Roles & Permissions)

---

## 📖 Full Documentation

For complete documentation, see:

- `DEPLOYMENT.md` - Complete deployment guide
- `README.md` - Application overview
- `IMS_2.0_HANDOVER_SUMMARY.md` - Project details

---

## 🚀 Production Deployment

For production deployment:

1. **Setup SSL certificates:**
   ```bash
   cd nginx/ssl
   # Follow instructions in nginx/ssl/README.md
   ```

2. **Update environment:**
   ```env
   NODE_ENV=production
   VITE_API_URL=https://your-domain.com/api/v1
   ENABLE_API_DOCS=false
   ```

3. **Deploy with production profile:**
   ```bash
   docker compose --profile production up -d
   ```

4. **Setup backups:**
   ```bash
   # Add to crontab for daily backups
   crontab -e
   # Add: 0 2 * * * cd /path/to/ims-2.0-core && ./scripts/backup.sh
   ```

5. **Enable firewall:**
   ```bash
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```

---

## 💡 Tips

- **Use strong passwords** for production
- **Enable HTTPS** for production deployments
- **Backup regularly** using `./scripts/backup.sh`
- **Monitor logs** for errors or issues
- **Update regularly** for security patches

---

## 📞 Need Help?

- See `DEPLOYMENT.md` for detailed troubleshooting
- Check Docker logs: `docker compose logs`
- Verify health: `curl http://localhost:8000/health`

---

**Version:** 2.0.0
**Last Updated:** 2026-01-22

**Ready to go? Start with `./scripts/deploy.sh`**
