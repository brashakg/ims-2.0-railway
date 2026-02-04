#!/bin/bash
# ============================================================================
# IMS 2.0 - Initial Setup Script
# ============================================================================
# This script prepares the environment for first-time deployment
# ============================================================================

set -e

echo "============================================================================"
echo "IMS 2.0 - Initial Setup"
echo "============================================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Docker is installed
echo -n "Checking Docker installation... "
if ! command -v docker &> /dev/null; then
    echo -e "${RED}FAILED${NC}"
    echo "Docker is not installed. Please install Docker first."
    echo "Visit: https://docs.docker.com/get-docker/"
    exit 1
fi
echo -e "${GREEN}OK${NC}"

# Check if Docker Compose is installed
echo -n "Checking Docker Compose installation... "
if ! command -v docker compose &> /dev/null && ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}FAILED${NC}"
    echo "Docker Compose is not installed. Please install Docker Compose first."
    echo "Visit: https://docs.docker.com/compose/install/"
    exit 1
fi
echo -e "${GREEN}OK${NC}"

# Create .env file from template if it doesn't exist
echo -n "Setting up environment configuration... "
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${GREEN}CREATED${NC}"
    echo ""
    echo -e "${YELLOW}IMPORTANT:${NC} Please edit the .env file and update the following:"
    echo "  - MONGO_USERNAME and MONGO_PASSWORD"
    echo "  - JWT_SECRET_KEY (generate with: openssl rand -hex 32)"
    echo "  - VITE_API_URL (your production API URL)"
    echo ""
    echo "Press ENTER to continue after updating .env file..."
    read
else
    echo -e "${YELLOW}EXISTS${NC} (using existing .env)"
fi

# Create required directories
echo -n "Creating required directories... "
mkdir -p backend/logs backend/uploads backend/backups nginx/logs nginx/ssl
echo -e "${GREEN}OK${NC}"

# Generate JWT secret if not set
if grep -q "CHANGE_THIS_IN_PRODUCTION" .env; then
    echo -e "${YELLOW}WARNING:${NC} JWT_SECRET_KEY is still set to default!"
    echo -n "Generate a random JWT secret? (y/n): "
    read -r generate_secret
    if [ "$generate_secret" = "y" ]; then
        NEW_SECRET=$(openssl rand -hex 32)
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS
            sed -i '' "s/CHANGE_THIS_IN_PRODUCTION/$NEW_SECRET/" .env
        else
            # Linux
            sed -i "s/CHANGE_THIS_IN_PRODUCTION/$NEW_SECRET/" .env
        fi
        echo -e "${GREEN}JWT secret generated and saved to .env${NC}"
    fi
fi

echo ""
echo "============================================================================"
echo -e "${GREEN}Setup Complete!${NC}"
echo "============================================================================"
echo ""
echo "Next steps:"
echo "  1. Review and update the .env file with your configuration"
echo "  2. Run './scripts/deploy.sh' to start the application"
echo ""
