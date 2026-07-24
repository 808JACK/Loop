#!/bin/bash
# Start webhook server in background for receiving Git merge events

cd /home/sarthakbehare/FLUX

# Kill any existing webhook server on port 8000
lsof -ti:8000 | xargs kill -9 2>/dev/null || true

# Start webhook server in background
nohup uv run python main.py --serve > logs/webhook_server.log 2>&1 &

WEBHOOK_PID=$!
echo "Webhook server started with PID: $WEBHOOK_PID"
echo "Logs: logs/webhook_server.log"
echo "Webhook endpoints:"
echo "  - GitHub: http://localhost:8000/api/v1/webhook/github"
echo "  - GitLab: http://localhost:8000/api/v1/webhook/gitlab"
echo "  - Bitbucket: http://localhost:8000/api/v1/webhook/bitbucket"
echo ""
echo "To stop the server: kill $WEBHOOK_PID"
