# IMS 2.0 - Deployment Guide

Complete deployment guide for IMS 2.0 - Retail Operating System

---

## 📋 Table of Contents

1. [System Requirements](#system-requirements)
2. [Quick Start](#quick-start)
3. [Configuration](#configuration)
4. [Deployment Options](#deployment-options)
5. [Database Setup](#database-setup)
6. [SSL/HTTPS Setup](#sslhttps-setup)
7. [Monitoring & Maintenance](#monitoring--maintenance)
8. [Troubleshooting](#troubleshooting)
9. [Security Best Practices](#security-best-practices)

---

## 🖥️ System Requirements

### Minimum Requirements

- **CPU:** 2 cores
- **RAM:** 4 GB
- **Storage:** 20 GB SSD
- **OS:** Linux (Ubuntu 20.04+, Debian 11+, CentOS 8+), macOS, or Windows with WSL2

### Recommended for Production

- **CPU:** 4+ cores
- **RAM:** 8+ GB
- **Storage:** 50+ GB SSD
- **OS:** Ubuntu 22.04 LTS or Debian 12

### Software Requirements

- Docker 24.0+
- Docker Compose 2.20+
- Git (for deployment)

---

## 🚀 Quick Start

### 1. Install Docker

**Ubuntu/Debian:**
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

**macOS:**
Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/)

**Windows:**
Install [Docker Desktop with WSL2](https://docs.docker.com/desktop/windows/wsl/)

### 2. Clone/Extract the Application

```bash
# If from Git repository
git clone https://github.com/your-org/ims-2.0.git
cd ims-2.0/ims-2.0-core

# If from ZIP file
unzip ims-2.0-deployment.zip
cd ims-2.0-core
```

### 3. Run Setup Script

```bash
chmod +x scripts/*.sh
./scripts/setup.sh
```

This will:
- Check Docker installation
- Create `.env` from `.env.example`
- Create required directories
- Generate JWT secret (optional)

### 4. Configure Environment

Edit the `.env` file:

```bash
nano .env
```

**Critical settings to update:**
- `MONGO_USERNAME` and `MONGO_PASSWORD`
- `JWT_SECRET_KEY` (generate with: `openssl rand -hex 32`)
- `VITE_API_URL` (your production API URL)

### 5. Deploy the Application

```bash
./scripts/deploy.sh
```

This will:
- Build Docker images
- Start all services (MongoDB, Backend, Frontend)
- Initialize the database
- Run health checks

### 6. Access the Application

- **Frontend:** http://localhost
- **Backend API:** http://localhost:8000
- **API Documentation:** http://localhost:8000/docs

**Default Login:**
- Username: `admin`
- Password: `admin123`

⚠️ **CHANGE THE DEFAULT PASSWORD IMMEDIATELY!**

---

## ⚙️ Configuration

### Environment Variables

All configuration is in the `.env` file. Key sections:

#### Application Settings
```env
NODE_ENV=production
API_PORT=8000
FRONTEND_PORT=80
```

#### Database Settings
```env
MONGO_HOST=mongodb
MONGO_PORT=27017
MONGO_DATABASE=ims_2_0
MONGO_USERNAME=your_username
MONGO_PASSWORD=your_secure_password
```

#### Security Settings
```env
JWT_SECRET_KEY=your_random_secret_key_here
CORS_ORIGINS=https://yourdomain.com
ENABLE_RATE_LIMITING=true
```

#### Optional Features
```env
ENABLE_AI_INSIGHTS=false
ENABLE_MARKETPLACE=false
ENABLE_INTEGRATIONS=true
```

See `.env.example` for all available options.

---

## 🌐 Deployment Options

### Option 1: Docker Compose (Recommended for Self-Hosting)

**Development:**
```bash
./scripts/deploy.sh
```

**Production with Rebuild:**
```bash
./scripts/deploy.sh --rebuild
```

**Production with Nginx SSL Proxy:**
```bash
# Enable production profile
docker compose --profile production up -d
```

### Option 2: Individual Services

**Backend Only:**
```bash
cd backend
docker build -t ims2-backend .
docker run -p 8000:8000 --env-file ../.env ims2-backend
```

**Frontend Only:**
```bash
cd frontend
docker build -t ims2-frontend .
docker run -p 80:80 -e VITE_API_URL=http://localhost:8000/api/v1 ims2-frontend
```

### Option 3: Cloud Platforms

#### Railway.app

1. Install Railway CLI: `npm i -g @railway/cli`
2. Login: `railway login`
3. Initialize: `railway init`
4. Add MongoDB: `railway add mongodb`
5. Deploy: `railway up`

#### Render.com

1. Create account at [render.com](https://render.com)
2. Connect GitHub repository
3. Create services:
   - **Backend:** Docker service using `backend/Dockerfile`
   - **Frontend:** Static site using `frontend/`
   - **Database:** MongoDB instance
4. Set environment variables in Render dashboard

#### DigitalOcean App Platform

1. Create App from GitHub repository
2. Configure services:
   - Backend: Dockerfile `backend/Dockerfile`
   - Frontend: Static site
3. Add MongoDB managed database
4. Deploy

### Option 4: Traditional VPS

**Using Ubuntu Server:**

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh

# 2. Clone repository
git clone <repo-url>
cd ims-2.0-core

# 3. Setup and deploy
./scripts/setup.sh
./scripts/deploy.sh

# 4. Setup firewall
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# 5. Setup auto-restart (systemd)
sudo nano /etc/systemd/system/ims2.service
```

**systemd service file:**
```ini
[Unit]
Description=IMS 2.0 Application
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/path/to/ims-2.0-core
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ims2
sudo systemctl start ims2
```

---

## 💾 Database Setup

### Automatic Initialization

On first run, the database is automatically initialized with:
- All collections
- Indexes for performance
- Default admin user

### Manual Database Initialization

If needed, you can manually initialize:

```bash
docker compose exec mongodb mongosh ims_2_0 /docker-entrypoint-initdb.d/init-mongo.js
```

### Database Backup

**Create Backup:**
```bash
./scripts/backup.sh
```

Backups are stored in `./backups/` with timestamp.

**Restore Backup:**
```bash
./scripts/restore.sh ./backups/ims2_backup_YYYYMMDD_HHMMSS.archive.gz
```

### Database Access

**Connect to MongoDB:**
```bash
docker compose exec mongodb mongosh -u admin -p changeme --authenticationDatabase admin
```

**View Collections:**
```javascript
use ims_2_0
show collections
db.users.find().pretty()
```

---

## 🔒 SSL/HTTPS Setup

### Development (Self-Signed Certificate)

```bash
cd nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout key.pem -out cert.pem \
  -subj "/C=IN/ST=State/L=City/O=IMS2/CN=localhost"
```

### Production (Let's Encrypt)

**Prerequisites:**
- Domain name pointing to your server
- Ports 80 and 443 open

**Setup:**
```bash
# 1. Install certbot
sudo apt-get update
sudo apt-get install certbot

# 2. Stop services temporarily
./scripts/stop.sh

# 3. Get certificate
sudo certbot certonly --standalone -d yourdomain.com -d www.yourdomain.com

# 4. Copy certificates
sudo cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem nginx/ssl/cert.pem
sudo cp /etc/letsencrypt/live/yourdomain.com/privkey.pem nginx/ssl/key.pem
sudo chmod 644 nginx/ssl/cert.pem
sudo chmod 600 nginx/ssl/key.pem

# 5. Enable production profile and restart
docker compose --profile production up -d
```

**Auto-Renewal:**
```bash
# Add to crontab
sudo crontab -e

# Add this line (renew at 2 AM daily)
0 2 * * * certbot renew --quiet --post-hook "docker compose -f /path/to/ims-2.0-core/docker-compose.yml restart nginx"
```

---

## 📊 Monitoring & Maintenance

### View Logs

**All services:**
```bash
docker compose logs -f
```

**Specific service:**
```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f mongodb
```

**Last 100 lines:**
```bash
docker compose logs --tail=100 backend
```

### Check Service Status

```bash
docker compose ps
```

### Service Management

**Restart services:**
```bash
docker compose restart
docker compose restart backend  # Specific service
```

**Stop services:**
```bash
./scripts/stop.sh
```

**Stop and remove data:**
```bash
./scripts/stop.sh --volumes  # ⚠️ DELETES ALL DATA!
```

### Update Application

```bash
# 1. Pull latest changes
git pull origin main

# 2. Backup database
./scripts/backup.sh

# 3. Rebuild and deploy
./scripts/deploy.sh --rebuild

# 4. Verify deployment
docker compose ps
curl http://localhost:8000/health
```

### Resource Usage

**Monitor resource usage:**
```bash
docker stats
```

**Clean up unused resources:**
```bash
docker system prune -a
```

---

## 🔍 Troubleshooting

### Services Won't Start

**Check logs:**
```bash
docker compose logs
```

**Common issues:**

1. **Port already in use:**
   ```bash
   # Check what's using the port
   sudo lsof -i :8000
   sudo lsof -i :80

   # Change port in .env file
   API_PORT=8001
   FRONTEND_PORT=8080
   ```

2. **Permission denied:**
   ```bash
   sudo chown -R $USER:$USER .
   chmod +x scripts/*.sh
   ```

3. **MongoDB connection failed:**
   - Check MongoDB is running: `docker compose ps mongodb`
   - Verify credentials in `.env`
   - Check MongoDB logs: `docker compose logs mongodb`

### Cannot Access Frontend

1. **Check if running:**
   ```bash
   docker compose ps frontend
   curl http://localhost/health
   ```

2. **Check API URL:**
   - Verify `VITE_API_URL` in `.env`
   - Should be full URL: `http://localhost:8000/api/v1`

3. **Clear browser cache:**
   - Hard refresh: Ctrl+Shift+R (Linux/Windows) or Cmd+Shift+R (macOS)

### Database Issues

**Reset database:**
```bash
# ⚠️ THIS DELETES ALL DATA!
docker compose down -v
docker compose up -d
```

**Check database connectivity:**
```bash
docker compose exec backend python -c "
from database.connection import init_db
print('✓ Connected' if init_db() else '✗ Failed')
"
```

### Performance Issues

1. **Check resource usage:**
   ```bash
   docker stats
   ```

2. **Increase resources (Docker Desktop):**
   - Settings → Resources
   - Increase CPU and Memory allocation

3. **Database optimization:**
   ```bash
   # Rebuild indexes
   docker compose exec mongodb mongosh ims_2_0 --eval "db.runCommand({reIndex: 'users'})"
   ```

---

## 🛡️ Security Best Practices

### 1. Change Default Credentials

```bash
# Login to application
# Navigate to Settings → Change Password
# Change admin password immediately
```

### 2. Secure Environment Variables

```bash
# Never commit .env to version control
echo ".env" >> .gitignore

# Set proper permissions
chmod 600 .env
```

### 3. Use Strong Secrets

```bash
# Generate strong JWT secret
openssl rand -hex 32

# Generate strong MongoDB password
openssl rand -base64 32
```

### 4. Enable Firewall

```bash
# Ubuntu/Debian
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # HTTP
sudo ufw allow 443/tcp  # HTTPS
sudo ufw deny 8000/tcp  # Block direct backend access
sudo ufw deny 27017/tcp # Block direct MongoDB access
sudo ufw enable
```

### 5. Regular Updates

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Update Docker images
docker compose pull
./scripts/deploy.sh --rebuild
```

### 6. Enable Rate Limiting

In `.env`:
```env
ENABLE_RATE_LIMITING=true
RATE_LIMIT_PER_MINUTE=60
```

### 7. Backup Regularly

```bash
# Setup automated backups (cron)
crontab -e

# Add daily backup at 2 AM
0 2 * * * cd /path/to/ims-2.0-core && ./scripts/backup.sh
```

### 8. Monitor Logs

```bash
# Check for suspicious activity
docker compose logs backend | grep "401\|403\|500"

# Setup log rotation
sudo nano /etc/logrotate.d/docker-compose
```

### 9. Use HTTPS in Production

- Always use SSL/TLS certificates
- Redirect HTTP to HTTPS
- Use HSTS headers (already configured in nginx)

### 10. Restrict MongoDB Access

In `docker-compose.yml`, remove MongoDB port mapping for production:
```yaml
mongodb:
  # ports:
  #   - "27017:27017"  # Comment out in production
```

---

## 📞 Support

For issues and questions:

- **Documentation:** See `README.md` and `IMS_2.0_HANDOVER_SUMMARY.md`
- **Issues:** GitHub Issues
- **Email:** support@ims2.com

---

## 📝 Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [React Documentation](https://react.dev/)
- [MongoDB Documentation](https://docs.mongodb.com/)
- [Nginx Documentation](https://nginx.org/en/docs/)

---

**Version:** 2.0.0
**Last Updated:** 2026-01-22
