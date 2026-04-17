#!/usr/bin/env bash
# One command to run locally — SQLite, no Postgres, no manual .env needed.
set -e
cd "$(dirname "$0")"

# Minimal .env so the app and seed scripts behave the same way
if [ ! -f .env ]; then
  echo "Creating .env (SQLite mode — safe for local dev)."
  printf '%s\n' "USE_SQLITE_LOCAL=1" "SECRET_KEY=dev-local-change-me" > .env
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

# Respect existing PostgreSQL configuration when present.
# Only fall back to SQLite if no DB settings were provided in .env or the environment.
db_url="${DATABASE_URL:-$(read_env_value DATABASE_URL)}"
db_user="${DB_USER:-$(read_env_value DB_USER)}"
db_password="${DB_PASSWORD:-$(read_env_value DB_PASSWORD)}"
db_host="${DB_HOST:-$(read_env_value DB_HOST)}"
db_name="${DB_NAME:-$(read_env_value DB_NAME)}"

if [ -z "$db_url" ] && [ -z "$db_user" ] && [ -z "$db_password" ] && [ -z "$db_host" ] && [ -z "$db_name" ]; then
  export USE_SQLITE_LOCAL=1
else
  # Prevent stale shell values from forcing SQLite when PostgreSQL is configured.
  unset USE_SQLITE_LOCAL
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

exec .venv/bin/python app.py
