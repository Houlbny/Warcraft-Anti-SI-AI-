#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
command -v python3 >/dev/null || { echo 'Python 3.11+ is required; install it explicitly.'; exit 1; }
command -v npm >/dev/null || { echo 'Node.js 22+ is required; install it explicitly.'; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
(cd frontend && npm ci && npm run build)
echo 'Ready. Run sh start.sh and open the local URL printed by the launcher.'
