#!/bin/bash
# ============================================================================
# IMS 2.0 - Database Restore Script
# ============================================================================
# Restores a MongoDB database backup
# ============================================================================

set -e

echo "============================================================================"
echo "IMS 2.0 - Database Restore"
echo "============================================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if backup file is provided
if [ -z "$1" ]; then
    echo -e "${RED}ERROR:${NC} No backup file specified!"
    echo "Usage: $0 <backup_file.archive.gz>"
    echo ""
    echo "Available backups:"
    ls -lh ./backups/ims2_backup_*.archive.gz 2>/dev/null || echo "  No backups found"
    exit 1
fi

BACKUP_FILE="$1"

# Check if backup file exists
if [ ! -f "$BACKUP_FILE" ]; then
    echo -e "${RED}ERROR:${NC} Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo -e "${RED}ERROR:${NC} .env file not found!"
    exit 1
fi

echo -e "${YELLOW}WARNING:${NC} This will replace all data in the database!"
echo "  Database: ${MONGO_DATABASE:-ims_2_0}"
echo "  Backup: $BACKUP_FILE"
echo ""
echo -n "Are you sure you want to continue? (type 'yes' to confirm): "
read -r confirmation

if [ "$confirmation" != "yes" ]; then
    echo "Restore cancelled."
    exit 0
fi

# Check if MongoDB container is running
if ! docker compose ps mongodb | grep -q "running"; then
    echo -e "${RED}ERROR:${NC} MongoDB container is not running!"
    echo "Start the services first: ./scripts/deploy.sh"
    exit 1
fi

echo ""
echo "Restoring backup..."

# Copy backup to container
docker compose cp "$BACKUP_FILE" mongodb:/tmp/restore.archive.gz

# Restore using mongorestore
docker compose exec -T mongodb mongorestore \
    --username="${MONGO_USERNAME:-admin}" \
    --password="${MONGO_PASSWORD:-changeme}" \
    --authenticationDatabase=admin \
    --db="${MONGO_DATABASE:-ims_2_0}" \
    --archive="/tmp/restore.archive.gz" \
    --gzip \
    --drop

# Remove temporary file from container
docker compose exec -T mongodb rm /tmp/restore.archive.gz

echo ""
echo "============================================================================"
echo -e "${GREEN}Restore Complete!${NC}"
echo "============================================================================"
echo ""
echo "Database has been restored from: $BACKUP_FILE"
echo ""
