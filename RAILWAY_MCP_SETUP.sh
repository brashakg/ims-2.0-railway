#!/bin/bash

###############################################################################
# IMS 2.0 - Railway MCP Setup Script
# This script sets up Claude Code to connect to your Railway deployment
# Run this on your local machine (not in Claude Code)
###############################################################################

set -e  # Exit on any error

echo "🚀 Starting Railway MCP Setup for IMS 2.0..."
echo ""

# =============================================================================
# TASK 1: INSTALL RAILWAY CLI
# =============================================================================

echo "📦 STEP 1: Installing Railway CLI..."
npm install -g @railway/cli
echo "✅ Railway CLI installed"
echo ""

# =============================================================================
# TASK 2: AUTHENTICATE WITH RAILWAY
# =============================================================================

echo "🔐 STEP 2: Authenticating with Railway..."
echo ""
echo "You will be given a pairing code. Use it to login at:"
echo "  https://railway.app/?code=<PAIRING_CODE>"
echo ""
echo "Running: railway login --browserless"
railway login --browserless
echo "✅ Authenticated with Railway"
echo ""

# =============================================================================
# TASK 3: VERIFY RAILWAY CLI WORKS
# =============================================================================

echo "🔍 STEP 3: Verifying Railway CLI..."
echo ""
echo "Railroad version:"
railway version
echo ""
echo "Railroad status:"
railway status
echo ""
echo "✅ Railway CLI is working"
echo ""

# =============================================================================
# TASK 4: LINK TO IMS 2.0 PROJECT
# =============================================================================

echo "🔗 STEP 4: Linking to IMS 2.0 project..."
echo ""
echo "Project: brashakg/ims-2.0-railway"
echo "Backend Service ID: 1ddba6c2-e9cb-47df-8bd6-54ddc9761be3"
echo ""

# Navigate to backend directory if we're not already there
cd "$(dirname "$0")/backend" 2>/dev/null || cd "$(dirname "$0")" || true

railway link --project b9ccf10c-66d9-4632-90a7-98f6f5a23efa --environment production --service 1ddba6c2-e9cb-47df-8bd6-54ddc9761be3

echo "✅ Linked to IMS 2.0 project"
echo ""

# =============================================================================
# TASK 5: ADD RAILWAY MCP SERVER TO CLAUDE CODE
# =============================================================================

echo "🤖 STEP 5: Adding Railway MCP Server to Claude Code..."
echo ""
echo "Running: claude mcp add railway-mcp-server -- npx -y @railway/mcp-server"
echo ""

claude mcp add railway-mcp-server -- npx -y @railway/mcp-server

echo "✅ Railway MCP Server added to Claude Code"
echo ""

# =============================================================================
# TASK 6: VERIFY MCP CONNECTION
# =============================================================================

echo "✨ STEP 6: Verifying MCP Connection..."
echo ""
echo "Restart Claude Code to load the new MCP server."
echo ""
echo "After restart, run this in Claude Code to verify:"
echo "  claude mcp list"
echo ""
echo "You should see: railway-mcp-server with tools like:"
echo "  - check-railway-status"
echo "  - list-services"
echo "  - get-logs"
echo "  - list-variables"
echo "  - deploy"
echo ""

# =============================================================================
# SETUP COMPLETE
# =============================================================================

echo "🎉 Railway MCP Setup Complete!"
echo ""
echo "Next steps:"
echo "1. Restart Claude Code"
echo "2. In Claude Code, ask: '@railway-mcp-server check-railway-status'"
echo "3. Then ask: 'Get logs from the ims-2.0-railway service'"
echo "4. Share the logs with me to diagnose the 503 error"
echo ""
