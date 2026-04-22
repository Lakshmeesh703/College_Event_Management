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
  local value
  value="$(grep -E "^${key}=" .env | tail -n 1 | cut -d '=' -f2-)"
  # Normalize .env parsing: remove CRLF residue, trim outer spaces, drop matching quotes.
  value="${value//$'\r'/}"
  value="${value#${value%%[![:space:]]*}}"
  value="${value%${value##*[![:space:]]}}"
  if [ "${value#\"}" != "$value" ] && [ "${value%\"}" != "$value" ]; then
    value="${value#\"}"
    value="${value%\"}"
  fi
  if [ "${value#\'}" != "$value" ] && [ "${value%\'}" != "$value" ]; then
    value="${value#\'}"
    value="${value%\'}"
  fi
  printf '%s' "$value"
}

if [ ! -d .venv ]; then
  echo "First-time setup: creating venv and installing packages (may take a minute)..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  echo "Setup done."
fi

export SECRET_KEY="${SECRET_KEY:-dev-local-change-me}"

# Keep local SMTP config deterministic: prefer .env values and clear stale shell values.
for key in \
  MAIL_SERVER MAIL_HOST MAIL_PORT MAIL_USE_TLS MAIL_USE_SSL \
  EMAIL_USER EMAIL_PASS \
  MAIL_USERNAME MAIL_USER MAIL_PASSWORD MAIL_APP_PASSWORD MAIL_DEFAULT_SENDER MAIL_FROM \
  SMTP_SERVER SMTP_HOST SMTP_PORT SMTP_USE_TLS SMTP_USE_SSL \
  SMTP_USERNAME SMTP_USER SMTP_PASSWORD SMTP_PASS SMTP_FROM \
  EMAIL_HOST EMAIL_PORT EMAIL_USE_TLS EMAIL_USE_SSL EMAIL_HOST_USER EMAIL_HOST_PASSWORD EMAIL_FROM \
  GMAIL_USER GMAIL_APP_PASSWORD
do
  val="$(read_env_value "$key")"
  if [ -n "$val" ]; then
    export "$key=$val"
  else
    unset "$key"
  fi
done

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
