#!/bin/sh
set -eu

if [ "$#" -gt 0 ] && [ "$1" != "autonomous" ]; then
  exec "$@"
fi

DATA_DIR="${DATA_DIR:-/data}"
PGDATA="${HOLYMOEX_PGDATA:-$DATA_DIR/postgres}"
RUNTIME_DIR="$DATA_DIR/runtime"
LOG_DIR="$DATA_DIR/logs"
PGPORT="${PGPORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-moex_agent}"
POSTGRES_USER="${POSTGRES_USER:-moex_agent}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-moex_agent_password}"

export APP_ENV="${APP_ENV:-production}"
export SYSTEM_MODE="${SYSTEM_MODE:-automatic_live_trading}"
export RUN_MODE="${RUN_MODE:-live_trading}"
export ARENA_GO_SANDBOX="${ARENA_GO_SANDBOX:-true}"
export SAFE_LIVE_SUBMIT="${SAFE_LIVE_SUBMIT:-false}"
export SINGLE_SCHEDULER_INSTANCE="${SINGLE_SCHEDULER_INSTANCE:-true}"

mkdir -p "$PGDATA" "$RUNTIME_DIR" "$LOG_DIR" "$DATA_DIR/audit" "$DATA_DIR/request_logs"
chown -R postgres:postgres "$DATA_DIR"

PG_BIN="$(dirname "$(find /usr/lib/postgresql -name initdb | sort | tail -n 1)")"
if [ ! -x "$PG_BIN/postgres" ]; then
  echo "PostgreSQL binaries were not found" >&2
  exit 1
fi

if [ ! -f "$PGDATA/PG_VERSION" ]; then
  echo "initializing PostgreSQL in $PGDATA"
  su postgres -c "$PG_BIN/initdb -D '$PGDATA' --auth-local=trust --auth-host=md5" >/dev/null
fi

echo "starting local PostgreSQL on 127.0.0.1:$PGPORT"
su postgres -c "$PG_BIN/pg_ctl -D '$PGDATA' -l '$LOG_DIR/postgres.log' -o '-c listen_addresses=127.0.0.1 -p $PGPORT' -w start" >/dev/null

cleanup() {
  su postgres -c "$PG_BIN/pg_ctl -D '$PGDATA' -m fast -w stop" >/dev/null 2>&1 || true
}
trap cleanup INT TERM EXIT

until pg_isready -p "$PGPORT" -U postgres >/dev/null 2>&1; do
  sleep 1
done

LOCAL_DATABASE_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:$PGPORT/$POSTGRES_DB"
if [ "${HOLYMOEX_USE_EXTERNAL_DATABASE:-}" = "true" ] || [ "${HOLYMOEX_USE_EXTERNAL_DATABASE:-}" = "1" ]; then
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "HOLYMOEX_USE_EXTERNAL_DATABASE is set but DATABASE_URL is empty" >&2
    exit 1
  fi
else
  export DATABASE_URL="$LOCAL_DATABASE_URL"
fi

if [ -z "${DATABASE_URL:-}" ]; then
  export DATABASE_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:$PGPORT/$POSTGRES_DB"
fi

su postgres -c "psql -p '$PGPORT' -v ON_ERROR_STOP=1 --dbname postgres <<'SQL'
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$POSTGRES_USER') THEN
    CREATE ROLE $POSTGRES_USER LOGIN PASSWORD '$POSTGRES_PASSWORD';
  ELSE
    ALTER ROLE $POSTGRES_USER LOGIN PASSWORD '$POSTGRES_PASSWORD';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE $POSTGRES_DB OWNER $POSTGRES_USER'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB')\gexec
GRANT ALL PRIVILEGES ON DATABASE $POSTGRES_DB TO $POSTGRES_USER;
SQL" >/dev/null

echo "applying database migrations"
python -m agent_app.storage.postgres.apply_migrations

RUNTIME_ENV_FILE="$RUNTIME_DIR/arena_go.env"
echo "running startup preflight"
python -m agent_app.server_startup --runtime-env-file "$RUNTIME_ENV_FILE"

if [ -f "$RUNTIME_ENV_FILE" ]; then
  . "$RUNTIME_ENV_FILE"
  export ARENA_GO_BOT_NAME ARENA_GO_PORTFOLIO LIVE_READINESS_PASSED
fi

if [ "${HOLYMOEX_STARTUP_ONCE:-}" = "true" ] || [ "${HOLYMOEX_STARTUP_ONCE:-}" = "1" ]; then
  echo "startup smoke completed; exiting because HOLYMOEX_STARTUP_ONCE is set"
  exit 0
fi

echo "starting autonomous scheduler"
exec python -m agent_app.scheduler --system-mode "$RUN_MODE" --interval-seconds "${SCHEDULER_INTERVAL_SECONDS:-60}"
