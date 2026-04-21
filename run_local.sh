#!/usr/bin/env bash
# One command to run locally with Postgres/Supabase configuration.
set -e
cd "$(dirname "$0")"

# Minimal .env so the app and seed scripts behave the same way
if [ ! -f .env ]; then
  echo "Creating .env with SECRET_KEY only. Add DATABASE_URL or full DB_* values before running."
  printf '%s\n' "SECRET_KEY=dev-local-change-me" > .env
fi

read_env_value() {
  local key="$1"
  if [ ! -f .env ]; then
    return 0
  fi
  grep -E "^${key}=" .env | tail -n 1 | cut -d '=' -f2-
}

if [ ! -d .venv ]; then
  echo "First-time setup: creating venv and installing packages (may take a minute)..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  echo "Setup done."
fi

export SECRET_KEY="${SECRET_KEY:-dev-local-change-me}"

# Ensure PostgreSQL/Supabase settings are present.
db_url="${DATABASE_URL:-$(read_env_value DATABASE_URL)}"
db_user="${DB_USER:-$(read_env_value DB_USER)}"
db_password="${DB_PASSWORD:-$(read_env_value DB_PASSWORD)}"
db_host="${DB_HOST:-$(read_env_value DB_HOST)}"
db_port="${DB_PORT:-$(read_env_value DB_PORT)}"
db_name="${DB_NAME:-$(read_env_value DB_NAME)}"

if [ -z "$db_url" ]; then
  if [ -z "$db_user" ] || [ -z "$db_password" ] || [ -z "$db_host" ] || [ -z "$db_port" ] || [ -z "$db_name" ]; then
    echo "Missing database configuration."
    echo "Set DATABASE_URL, or set all DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME in .env or environment."
    exit 1
  fi
fi

# Use provided PORT, or PORT from .env, or default to 5000.
if [ -z "${PORT:-}" ]; then
  PORT="$(read_env_value PORT)"
fi
if [ -z "${PORT:-}" ]; then
  PORT="5000"
fi

# Auto-cleanup: kill any old app instance on the target port before starting.
if command -v ss >/dev/null 2>&1 && command -v pgrep >/dev/null 2>&1; then
  old_pids="$(ss -ltnp 2>/dev/null | sed -n "s/.*:${PORT} .*pid=\([0-9]\+\).*/\1/p" | sort -u)"
  if [ -n "$old_pids" ]; then
    echo "Stopping old Flask process(es) on port ${PORT}..."
    kill $old_pids 2>/dev/null || true
  fi
fi

export PORT

echo ""
echo "  → Open in browser:  http://127.0.0.1:${PORT}"
echo "  → Stop server:      Ctrl+C"
echo ""

exec .venv/bin/python -m backend.start
