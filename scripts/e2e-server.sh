#!/usr/bin/env bash
# The server the browser tests (web/e2e) run against: the real API and the built frontend
# (web/dist, from `npm run build`), on a fresh SQLite database seeded by e2e_seed.py, with
# no provider keys, so no market data provider is called. Playwright starts it; the port
# is E2E_PORT (8765 by default), and the test account's key is written to
# web/e2e/.data/api-key.

set -euo pipefail
cd "$(dirname "$0")/.."

python="${PYTHON:-}"
if [[ -z "$python" ]]; then
    if [[ -x venv/bin/python ]]; then python=venv/bin/python; else python=python; fi
fi

data="$PWD/web/e2e/.data"
rm -rf "$data"
mkdir -p "$data"

# Settings from the environment override .env (an empty one does not), so a local .env's
# database is not touched; CI has no .env at all.
export DATABASE_URL="sqlite+aiosqlite:///$data/e2e.db"
export YFINANCE_FALLBACK=false
export EMAIL_TO_CONSOLE=true

PYTHONPATH="$PWD" "$python" scripts/e2e_seed.py "$data/api-key"
exec "$python" -m uvicorn app.main:app --host 127.0.0.1 --port "${E2E_PORT:-8765}"
