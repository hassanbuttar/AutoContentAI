#!/usr/bin/env bash
# AutoContent AI - Linux installer (Ubuntu 22.04+). Usage: bash scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
[ -d backend/.venv ] || "$PY" -m venv backend/.venv
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -q -r backend/requirements.txt
command -v espeak-ng >/dev/null || echo "Tip: sudo apt install espeak-ng   (local narration on Linux)"
(cd frontend && npm install --no-audit --no-fund && npm run build)
[ -f .env ] || cp .env.example .env
echo "Installed. Start with: bash scripts/start.sh   ->  http://127.0.0.1:8000"
