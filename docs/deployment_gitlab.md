# HolyMOEX GitLab Deployment Notes

This project is prepared for organizer-managed server deployment with the root
`Dockerfile`.

## What The Agent Does

HolyMOEX runs a modular trading pipeline:

```text
data -> features -> decision -> risk -> execution -> portfolio -> analytics
```

The LLM layer is limited to text/event extraction and does not submit orders.
`Decision Engine Module` creates decision records. `Risk Control Module` is the
hard boundary before order intents. `Execution Engine Module` can submit to
ArenaGo only after risk approval and live-readiness gates.

## Required Environment

The organizer deployment replaces the ArenaGo key through:

```text
SANDBOX_API_KEY
```

ArenaGo auth resolution is:

1. `SANDBOX_API_KEY`
2. `ARENA_GO_API_KEY`, only for local/dev/test or when `ALLOW_ARENA_GO_TOKEN_FALLBACK=true`
3. `ARENA_GO_TOKEN`, only for local/dev/test or when `ALLOW_ARENA_GO_TOKEN_FALLBACK=true`

Do not store real keys in git. Use GitLab CI/CD Variables or server
environment variables for:

```text
SANDBOX_API_KEY
POLZA_API_KEY
```

`POLZA_API_KEY` is read from environment. If it is absent, startup disables LLM
text schedules and continues deterministic runtime without printing secrets.

## Safe Defaults

The image defaults are intentionally conservative:

```text
SAFE_LIVE_SUBMIT=false
LIVE_READINESS_PASSED=false
ARENA_GO_SHORTS_ALLOWED=false
DECISION_ALLOW_SHORT_SELLING=false
DECISION_ALLOW_LONG_TO_SHORT_FLIP=false
ALLOW_ARENA_GO_TOKEN_FALLBACK=false
DATA_DIR=/data
```

`RUN_MODE=live_trading` and `SYSTEM_MODE=automatic_live_trading` may be set in
the image, but real broker submit remains blocked until preflight exports
`LIVE_READINESS_PASSED=true` and the operator explicitly sets
`SAFE_LIVE_SUBMIT=true`.

## Persistent Data

The container uses `/data` for persistent runtime state:

```text
/data/postgres
/data/runtime
/data/logs
/data/audit
/data/request_logs
```

Mount `/data` as persistent storage on the organizer server.

## Build

```bash
docker build -t holymoex-agent .
```

## Local Safe Run

For local development only:

```bash
cp config/api_keys.example.env config/api_keys.local.env
docker compose -f docker/docker-compose.prod.yml up -d --build
```

`config/api_keys.local.env` is ignored by git and must never be committed.

## GitLab Pipeline

Use GitLab -> Build -> Pipelines -> New pipeline if the organizer UI requires a
manual pipeline run. The important artifact is the root `Dockerfile`; no local
env file is required for server deployment.

For optional ops buttons, set:

```text
ENABLE_OPS_BUTTONS=yes
```

## Stop And Logs

Local compose:

```bash
docker compose -f docker/docker-compose.prod.yml logs -f scheduler_worker
docker compose -f docker/docker-compose.prod.yml down
```

Single-container deployment:

```bash
docker logs -f <container>
docker stop <container>
```

## Pre-Commit Checks

```bash
python -m compileall -q agent_app tests
python -m pytest -q
docker build -t holymoex-agent:gitlab-ready .
```

Before pushing, verify:

```bash
git status --short
git ls-files config/api_keys.local.env
git grep -n -I -E "pza_|SANDBOX_API_KEY=.{20,}|ARENA_GO_TOKEN=.{20,}|POLZA_API_KEY=.{20,}|Bearer[[:space:]]|sk-[A-Za-z0-9_-]{16,}"
```

The second command must print nothing. The grep command may show placeholders
or documentation references, but must not show real keys.
