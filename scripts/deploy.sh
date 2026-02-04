#!/bin/bash
# ============================================================================
# IMS 2.0 - Deployment Script
# ============================================================================
# Builds and starts all services using Docker Compose
# ============================================================================

set -e

echo "============================================================================"
echo "IMS 2.0 - Deployment"
echo "============================================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}ERROR:${NC} .env file not found!"
    echo "Please run './scripts/setup.sh' first."
    exit 1
fi

# Load environment variables
set -a
source .env
set +a

# Parse command line arguments
BUILD_FLAG=""
DETACH_FLAG="-d"

while [[ $# -gt 0 ]]; do
    case $1 in
        --rebuild)
            BUILD_FLAG="--build"
            shift
            ;;
        --attach)
            DETACH_FLAG=""
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--rebuild] [--attach]"
            echo "  --rebuild: Force rebuild of Docker images"
            echo "  --attach: Run in foreground (default: background)"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}Starting IMS 2.0 deployment...${NC}"
echo ""

# Stop any running containers
echo "Stopping existing containers..."
docker compose down 2>/dev/null || true

# Pull latest base images
echo "Pulling latest base images..."
docker compose pull mongodb nginx 2>/dev/null || true

# Build and start services
echo ""
echo -e "${BLUE}Building and starting services...${NC}"
if [ -n "$BUILD_FLAG" ]; then
    echo "  (Rebuilding images from scratch)"
fi

docker compose up $BUILD_FLAG $DETACH_FLAG

if [ -n "$DETACH_FLAG" ]; then
    echo ""
    echo "============================================================================"
    echo -e "${GREEN}Deployment Complete!${NC}"
    echo "============================================================================"
    echo ""
    echo "Services are now running in the background."
    echo ""
    echo -e "${YELLOW}Access Points:${NC}"
    echo "  Frontend:   http://localhost:${FRONTEND_PORT:-80}"
    echo "  Backend:    http://localhost:${API_PORT:-8000}"
    echo "  API Docs:   http://localhost:${API_PORT:-8000}/docs"
    echo "  MongoDB:    mongodb://localhost:${MONGO_PORT:-27017}"
    echo ""
    echo -e "${YELLOW}Useful Commands:${NC}"
    echo "  View logs:        docker compose logs -f"
    echo "  View status:      docker compose ps"
    echo "  Stop services:    ./scripts/stop.sh"
    echo "  Restart:          docker compose restart"
    echo ""

    # Wait a moment for services to start
    sleep 5

    # Check service health
    echo "Checking service health..."
    docker compose ps
    echo ""
fi
