#!/bin/bash
# Stop webhook server

# Kill webhook server on port 8000
lsof -ti:8080 | xargs kill -9 2>/dev/null || true

echo "Webhook server stopped"
