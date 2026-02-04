#!/bin/sh
# ============================================================================
# IMS 2.0 Frontend - Docker Entrypoint Script
# ============================================================================
# Injects runtime environment variables into the built application
# ============================================================================

set -e

# Create env-config.js with runtime environment variables
cat > /usr/share/nginx/html/env-config.js <<EOF
window.ENV = {
  VITE_API_URL: "${VITE_API_URL:-http://localhost:8000/api/v1}",
  VITE_APP_NAME: "${VITE_APP_NAME:-IMS 2.0}",
  VITE_APP_VERSION: "${VITE_APP_VERSION:-2.0.0}"
};
EOF

echo "✅ Environment configuration injected"
echo "   API URL: ${VITE_API_URL:-http://localhost:8000/api/v1}"

# Execute the CMD
exec "$@"
