#!/bin/bash
# ============================================================================
# IMS 2.0 - Stop Script
# ============================================================================
# Stops all running services
# ============================================================================

set -e

echo "============================================================================"
echo "IMS 2.0 - Stopping Services"
echo "============================================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse command line arguments
REMOVE_VOLUMES=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --volumes)
            REMOVE_VOLUMES="-v"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--volumes]"
            echo "  --volumes: Remove volumes (WARNING: This deletes all data!)"
            exit 1
            ;;
    esac
done

if [ -n "$REMOVE_VOLUMES" ]; then
    echo -e "${RED}WARNING:${NC} This will remove all volumes and delete all data!"
    echo -n "Are you sure? (type 'yes' to confirm): "
    read -r confirmation
    if [ "$confirmation" != "yes" ]; then
        echo "Cancelled."
        exit 0
    fi
fi

# Stop services
echo "Stopping Docker containers..."
docker compose down $REMOVE_VOLUMES

echo ""
echo "============================================================================"
echo -e "${GREEN}Services Stopped${NC}"
echo "============================================================================"
echo ""

if [ -n "$REMOVE_VOLUMES" ]; then
    echo -e "${YELLOW}All data has been removed.${NC}"
else
    echo "To start services again, run: ./scripts/deploy.sh"
    echo "To remove all data, run: ./scripts/stop.sh --volumes"
fi
echo ""
