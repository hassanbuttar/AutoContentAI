#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p storage/logs
if [ -f storage/autocontent.pid ] && kill -0 "$(cat storage/autocontent.pid)" 2>/dev/null; then
  echo "Already running (PID $(cat storage/autocontent.pid))"; exit 0
fi
cd backend
nohup .venv/bin/python -m app >> ../storage/logs/stdout.log 2>&1 &
echo $! > ../storage/autocontent.pid
echo "Started (PID $!) -> http://127.0.0.1:8000"
