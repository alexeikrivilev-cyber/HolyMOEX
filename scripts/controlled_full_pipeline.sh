#!/bin/sh
set -eu

if [ -z "${DATABASE_URL:-}" ] || echo "$DATABASE_URL" | grep -q '@postgres_local:'; then
  export DATABASE_URL="postgresql://${POSTGRES_USER:-moex_agent}:${POSTGRES_PASSWORD:-moex_agent_password}@127.0.0.1:${PGPORT:-5432}/${POSTGRES_DB:-moex_agent}"
fi

export CONTROLLED_PIPELINE_MARKET_OPEN_OVERRIDE="${CONTROLLED_PIPELINE_MARKET_OPEN_OVERRIDE:-true}"
export SAFE_LIVE_SUBMIT="${SAFE_LIVE_SUBMIT:-false}"
export EXECUTION_PROVIDER="${EXECUTION_PROVIDER:-mock}"
export RUN_MODE="${RUN_MODE:-live_trading}"

python -m agent_app.staging_runner \
  --database-url "$DATABASE_URL" \
  --run-mode "$RUN_MODE" \
  --execution-provider "$EXECUTION_PROVIDER" \
  --instrument-cap "${STAGING_INSTRUMENT_CAP:-3}" \
  --max-news-items "${STAGING_MAX_NEWS_ITEMS:-1}"
