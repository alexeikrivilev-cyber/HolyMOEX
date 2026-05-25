#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
if command -v cygpath >/dev/null 2>&1; then
  DOCKER_ROOT_DIR="$(cygpath -w "$ROOT_DIR")"
  export MSYS2_ARG_CONV_EXCL="*"
else
  DOCKER_ROOT_DIR="$ROOT_DIR"
fi

IMAGE="${IMAGE:-holymoex:server}"
PY_IMAGE="${PY_IMAGE:-python:3.11-slim}"
PG_IMAGE="${PG_IMAGE:-postgres:16}"
USE_LOCAL_ENV="${DEPLOY_CHECK_USE_LOCAL_ENV:-true}"
LOCAL_ENV_FILE="config/api_keys.local.env"
TMP_DIR="$(mktemp -d)"
PG_CONTAINER="holymoex_deploy_check_pg_$$"
PG_NETWORK="holymoex_deploy_check_net_$$"
SMOKE_VOLUME="holymoex_deploy_check_data_$$"

cleanup() {
  docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$PG_NETWORK" >/dev/null 2>&1 || true
  docker volume rm "$SMOKE_VOLUME" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

log() {
  printf '\n[deploy-check] %s\n' "$*"
}

docker_env_args=()
if [ "$USE_LOCAL_ENV" = "true" ] && [ -f "$LOCAL_ENV_FILE" ]; then
  docker_env_args+=(--env-file "$LOCAL_ENV_FILE")
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "required command not found: $1" >&2
    exit 1
  }
}

secret_scan() {
  log "secret scan"
  required_ignored=(
    "config/api_keys.local.env"
    "config/example.local.env"
    ".env"
    ".env.local"
    "example.secret.env"
    "example.credentials"
    "example.token"
  )
  for path in "${required_ignored[@]}"; do
    git check-ignore -q "$path" || {
      echo "path is not ignored by git: $path" >&2
      exit 1
    }
  done

  for pattern in \
    "config/api_keys.local.env" "config/*.local.env" ".env" ".env.local" \
    "*.secret.*" "*secret*" "*credentials*" "*token*" "__pycache__" ".pytest_cache" ".git" "data/"; do
    grep -Fqx "$pattern" .dockerignore || {
      echo ".dockerignore missing pattern: $pattern" >&2
      exit 1
    }
  done

  if grep -RInE '(SANDBOX_API_KEY|ARENA_GO_TOKEN|POLZA_API_KEY)=([A-Za-z0-9_./+=:-]{20,})' \
      --exclude-dir=.git --exclude-dir=.pytest_cache --exclude-dir=__pycache__ --exclude-dir=data \
      --exclude='api_keys.local.env' --exclude='*.local.env' . \
      | grep -Ev 'replace_with_|your_|example_|<.*>'; then
    echo "possible committed secret found" >&2
    exit 1
  fi
}

ensure_dev_deps() {
  local py_cmd="$1"
  if ! "$py_cmd" -m pytest --version >/dev/null 2>&1; then
    log "pytest not found; installing requirements-dev.txt"
    "$py_cmd" -m pip install --no-cache-dir -r requirements-dev.txt >/tmp/holy_dev_deps_install.log || {
      echo "failed to install dev/test dependencies from requirements-dev.txt; see /tmp/holy_dev_deps_install.log" >&2
      exit 1
    }
  fi
}

python_checks() {
  log "compileall, unittest and pytest"
  if command -v python3 >/dev/null 2>&1; then
    ensure_dev_deps python3
    python3 -m compileall -q agent_app tests
    PYTHONPATH=. python3 -m unittest discover -s tests -p "test*.py" -v
    PYTHONPATH=. python3 -m pytest -q
  elif command -v python >/dev/null 2>&1; then
    ensure_dev_deps python
    python -m compileall -q agent_app tests
    PYTHONPATH=. python -m unittest discover -s tests -p "test*.py" -v
    PYTHONPATH=. python -m pytest -q
  else
    docker run --rm -v "$DOCKER_ROOT_DIR:/work" -w /work -e PYTHONPATH=/work "$PY_IMAGE" \
      sh -lc "pip install --no-cache-dir -r requirements-dev.txt >/tmp/holy_dev_deps_install.log && python -m compileall -q agent_app tests && python -m unittest discover -s tests -p 'test*.py' -v && python -m pytest -q"
  fi
}

build_image() {
  log "docker build"
  docker build --pull=false -t "$IMAGE" .
}

image_secret_scan() {
  log "built image secret scan"
  docker run --rm "$IMAGE" sh -lc '
    test ! -e /app/config/api_keys.local.env
    test ! -d /app/.git
    test ! -d /app/.pytest_cache
    test ! -d /app/__pycache__
    ! find /app -iname "*secret*" -o -iname "*credentials*" -o -iname "*token*" | grep .
  '
  if [ -n "${SANDBOX_API_KEY:-}" ]; then
    docker run --rm "$IMAGE" sh -lc "if grep -R \"${SANDBOX_API_KEY}\" /app 2>/dev/null; then exit 1; fi"
  fi
  if [ -n "${POLZA_API_KEY:-}" ]; then
    docker run --rm "$IMAGE" sh -lc "if grep -R \"${POLZA_API_KEY}\" /app 2>/dev/null; then exit 1; fi"
  fi
}

root_restart_smoke() {
  log "root container /data restart smoke"
  local first_log="$TMP_DIR/root_first.log"
  local second_log="$TMP_DIR/root_second.log"
  docker volume rm "$SMOKE_VOLUME" >/dev/null 2>&1 || true
  docker run --rm "${docker_env_args[@]}" \
    -e HOLYMOEX_STARTUP_ONCE=true \
    -e STARTUP_SKIP_EXTERNAL=true \
    -e SAFE_LIVE_SUBMIT=false \
    -e POSTGRES_PASSWORD=moex_agent_password \
    -v "$SMOKE_VOLUME:/data" "$IMAGE" >"$first_log"
  grep -q "apply 018_portfolio_identity_readiness_refinement.sql" "$first_log"
  grep -q '"readiness_passed": true' "$first_log"

  docker run --rm "${docker_env_args[@]}" \
    -e HOLYMOEX_STARTUP_ONCE=true \
    -e STARTUP_SKIP_EXTERNAL=true \
    -e SAFE_LIVE_SUBMIT=false \
    -e POSTGRES_PASSWORD=moex_agent_password \
    -v "$SMOKE_VOLUME:/data" "$IMAGE" >"$second_log"
  grep -q "skip 018_portfolio_identity_readiness_refinement.sql" "$second_log"
  grep -q '"readiness_passed": true' "$second_log"
}

arena_go_sync_smoke() {
  log "ArenaGo sync smoke without submit"
  local arena_log="$TMP_DIR/arena.log"
  docker run --rm "${docker_env_args[@]}" \
    -e HOLYMOEX_STARTUP_ONCE=true \
    -e SAFE_LIVE_SUBMIT=false \
    -e POSTGRES_PASSWORD=moex_agent_password \
    -v "$SMOKE_VOLUME:/data" "$IMAGE" >"$arena_log"
  grep -q '"readiness_passed": true' "$arena_log"
  grep -q '"broker_sync_status": "success"' "$arena_log"
  grep -q '"bot_name":' "$arena_log"
}

scheduler_smoke() {
  log "scheduler once smoke"
  local scheduler_log="$TMP_DIR/scheduler.log"
  docker run --rm "${docker_env_args[@]}" \
    -e SCHEDULER_ONCE=true \
    -e SCHEDULER_INTERVAL_SECONDS=1 \
    -e SCHEDULER_SCHEDULE_IDS=schedule:live_autonomous:portfolio_sync:1m \
    -e SCHEDULER_MAX_ENTRIES_PER_TICK=1 \
    -e SYSTEM_MODE=automatic_live_trading \
    -e RUN_MODE=live_trading \
    -e ARENA_GO_SANDBOX=true \
    -e SAFE_LIVE_SUBMIT=false \
    -e POSTGRES_PASSWORD=moex_agent_password \
    -v "$SMOKE_VOLUME:/data" "$IMAGE" >"$scheduler_log"
  grep -q "scheduler_entry_started" "$scheduler_log"
  grep -q "scheduler_entry_finished" "$scheduler_log"
  grep -q '"exit_code": 0' "$scheduler_log"
}

start_temp_postgres() {
  log "temp PostgreSQL"
  docker network create "$PG_NETWORK" >/dev/null
  docker run -d --name "$PG_CONTAINER" --network "$PG_NETWORK" \
    -e POSTGRES_DB=moex_agent \
    -e POSTGRES_USER=moex_agent \
    -e POSTGRES_PASSWORD=moex_agent_password \
    "$PG_IMAGE" >/dev/null
  for _ in $(seq 1 60); do
    if docker exec "$PG_CONTAINER" pg_isready -U moex_agent -d moex_agent >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "temp PostgreSQL did not become ready" >&2
  exit 1
}

postgres_url() {
  printf 'postgresql://moex_agent:moex_agent_password@%s:5432/moex_agent' "$PG_CONTAINER"
}

migration_readiness_and_staging() {
  log "migrations, readiness and controlled staging"
  local db_url
  db_url="$(postgres_url)"
  docker run --rm --network "$PG_NETWORK" "${docker_env_args[@]}" \
    -e DATABASE_URL="$db_url" "$IMAGE" python -m agent_app.storage.postgres.apply_migrations >/tmp/holy_migrations_1.log
  docker run --rm --network "$PG_NETWORK" "${docker_env_args[@]}" \
    -e DATABASE_URL="$db_url" "$IMAGE" python -m agent_app.storage.postgres.apply_migrations >/tmp/holy_migrations_2.log
  grep -q "apply 018_portfolio_identity_readiness_refinement.sql" /tmp/holy_migrations_1.log
  grep -q "skip 018_portfolio_identity_readiness_refinement.sql" /tmp/holy_migrations_2.log

  for view in audit.database_readiness_check audit.metric_weights_readiness_check audit.live_trading_readiness_check audit.allowed_universe_readiness_check; do
    local fails
    fails="$(docker exec "$PG_CONTAINER" psql -U moex_agent -d moex_agent -At -c "SELECT count(*) FROM $view WHERE status <> 'pass';")"
    test "$fails" = "0"
  done

  docker run --rm --network "$PG_NETWORK" "${docker_env_args[@]}" \
    -e DATABASE_URL="$db_url" \
    -e SAFE_LIVE_SUBMIT=false \
    -e CONTROLLED_PIPELINE_MARKET_OPEN_OVERRIDE=true \
    -e EXECUTION_PROVIDER=mock \
    "$IMAGE" python -m agent_app.staging_runner --instrument-cap 2 --max-news-items 1 >"$TMP_DIR/staging.log"
  grep -q "controlled_staging_completed" "$TMP_DIR/staging.log" || \
    docker exec "$PG_CONTAINER" psql -U moex_agent -d moex_agent -At -c \
      "SELECT 1 FROM audit.audit_record WHERE event_type='controlled_staging_completed' LIMIT 1" | grep -q 1
}

polza_healthcheck() {
  log "PolzaAI healthcheck"
  docker run --rm "${docker_env_args[@]}" -e PYTHONPATH=/app "$IMAGE" python - <<'PY'
from agent_app.contracts.unified_objects import CachePolicy, ExternalRequest, RetryPolicy
from agent_app.modules.external_request_gateway.service import ExternalRequestGatewayService
from agent_app.modules.external_request_gateway.repository import InMemoryExternalRequestGatewayRepository

svc = ExternalRequestGatewayService(repository=InMemoryExternalRequestGatewayRepository())
req = ExternalRequest(
    request_id="deploy_polza_models",
    caller_module="deploy_check",
    provider="polza_ai",
    request_type="models",
    universe_id="moex_top20_manual",
    instrument_ids=(),
    payload={},
    cache_policy=CachePolicy(use_cache=False, max_age_seconds=0, write_cache=False),
    timeout_ms=10000,
    retry_policy=RetryPolicy(max_retries=0, backoff_ms=0),
    idempotency_key="deploy_polza_models",
)
resp = svc.process(req)
if resp.status == "success":
    print("polza models success")
    raise SystemExit(0)

fallback = ExternalRequest(
    request_id="deploy_polza_json",
    caller_module="deploy_check",
    provider="polza_ai",
    request_type="llm_completion",
    universe_id="moex_top20_manual",
    instrument_ids=(),
    payload={
        "messages": [
            {"role": "system", "content": "Return only strict JSON."},
            {"role": "user", "content": "Return {\"schema_version\":\"1.0\",\"model_id\":\"healthcheck\",\"model_version\":\"unknown\",\"task_type\":\"healthcheck\",\"items\":[]}"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    },
    cache_policy=CachePolicy(use_cache=False, max_age_seconds=0, write_cache=False),
    timeout_ms=20000,
    retry_policy=RetryPolicy(max_retries=0, backoff_ms=0),
    idempotency_key="deploy_polza_json",
)
resp = svc.process(fallback)
if resp.status != "success":
    raise SystemExit(f"PolzaAI healthcheck failed: {resp.status} {resp.errors}")
print("polza json fallback success")
PY
}

main() {
  require_cmd docker
  secret_scan
  python_checks
  build_image
  image_secret_scan
  root_restart_smoke
  arena_go_sync_smoke
  scheduler_smoke
  start_temp_postgres
  migration_readiness_and_staging
  polza_healthcheck
  log "success"
}

main "$@"
