#!/bin/bash
# Start webhook server in background for receiving Git merge events

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

PORT=${1:-8080}

# Kill any existing webhook server on port $PORT
lsof -ti:$PORT | xargs kill -9 2>/dev/null || true

# Start webhook server in background
nohup uv run python main.py --serve --port $PORT > logs/webhook_server.log 2>&1 &

WEBHOOK_PID=$!
echo "Webhook server started with PID: $WEBHOOK_PID on port $PORT"
echo "Logs: logs/webhook_server.log"
echo "Webhook endpoints:"
echo "  - GitHub: http://localhost:$PORT/api/v1/webhook/github"
echo "  - GitLab: http://localhost:$PORT/api/v1/webhook/gitlab"
echo "  - Bitbucket: http://localhost:$PORT/api/v1/webhook/bitbucket"
echo ""
echo "To stop the server: kill $WEBHOOK_PID"
