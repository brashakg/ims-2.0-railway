#!/bin/bash
# ============================================================================
# IMS 2.0 - Database Backup Script
# ============================================================================
# Creates a backup of the MongoDB database
# ============================================================================

set -e

echo "============================================================================"
echo "IMS 2.0 - Database Backup"
echo "============================================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo -e "${RED}ERROR:${NC} .env file not found!"
    exit 1
fi

# Create backup directory
BACKUP_DIR="./backups"
mkdir -p "$BACKUP_DIR"

# Generate backup filename with timestamp
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/ims2_backup_$TIMESTAMP"

echo "Creating backup..."
echo "  Database: ${MONGO_DATABASE:-ims_2_0}"
echo "  Output: $BACKUP_FILE"
echo ""

# Check if MongoDB container is running
if ! docker compose ps mongodb | grep -q "running"; then
    echo -e "${RED}ERROR:${NC} MongoDB container is not running!"
    echo "Start the services first: ./scripts/deploy.sh"
    exit 1
fi

# Create backup using mongodump
docker compose exec -T mongodb mongodump \
    --username="${MONGO_USERNAME:-admin}" \
    --password="${MONGO_PASSWORD:-changeme}" \
    --authenticationDatabase=admin \
    --db="${MONGO_DATABASE:-ims_2_0}" \
    --archive="/tmp/backup.archive" \
    --gzip

# Copy backup from container to host
docker compose cp mongodb:/tmp/backup.archive "$BACKUP_FILE.archive.gz"

# Remove temporary file from container
docker compose exec -T mongodb rm /tmp/backup.archive

echo ""
echo "============================================================================"
echo -e "${GREEN}Backup Complete!${NC}"
echo "============================================================================"
echo ""
echo "Backup saved to: $BACKUP_FILE.archive.gz"
echo "Size: $(du -h "$BACKUP_FILE.archive.gz" | cut -f1)"
echo ""
echo "To restore this backup, run:"
echo "  ./scripts/restore.sh $BACKUP_FILE.archive.gz"
echo ""

# Clean up old backups (keep last 30 days)
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-30}
echo "Cleaning up backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "ims2_backup_*.archive.gz" -mtime +$RETENTION_DAYS -delete
echo -e "${GREEN}Cleanup complete.${NC}"
echo ""
