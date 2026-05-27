# Guarded ArenaGo Live Smoke Test

This runbook prepares a short, manually supervised ArenaGo live smoke test after:

- Profitability Guardrails v1;
- Decision Engine post-cost economics;
- analytics live validation views;
- controlled paper validation runbook.

It does not change trading logic, scoring, Risk Control, Execution Engine, weights, or strategy. The project does not currently expose a named `live_smoke_guarded` runtime profile; use the existing env/runtime gates and database risk policy, then stop the scheduler manually after the bounded window.

## Goal

Run a guarded ArenaGo live smoke for 30-60 minutes:

- small position size;
- strict daily stop;
- limited instrument exposure;
- analytics views monitored during the run;
- fast stop path available.

This is not a full long-running live deployment.

## What Is Configurable Today

Supported by env/config:

| Area | Current mechanism |
|---|---|
| run mode | `SYSTEM_MODE`, `RUN_MODE` |
| broker/gateway | `ARENA_GO_BASE_URL`, `ARENA_GO_SANDBOX`, `SANDBOX_API_KEY`, `ARENA_GO_TOKEN`, `ARENA_GO_PORTFOLIO`, `ARENA_GO_BOT_NAME` |
| live submit gate | `SAFE_LIVE_SUBMIT`, `LIVE_READINESS_PASSED` |
| universe id | `SELECTED_UNIVERSE_ID` |
| scheduler bounds | `SCHEDULER_ONCE`, `SCHEDULER_SCHEDULE_IDS`, `SCHEDULER_MAX_ENTRIES_PER_TICK`, `SCHEDULER_INTERVAL_SECONDS` |
| session probe | `ARENA_GO_SESSION_PROBE_*`, `MARKET_SESSION_SOURCE` |
| order quantity units | `ARENA_GO_SUBMIT_QUANTITY_UNITS` |

Supported by DB risk policy / instrument limits:

- `max_position_pct`;
- `max_order_value_rub`;
- `max_daily_loss_pct`;
- `max_daily_loss_limit`;
- `max_allowed_slippage_bps`;
- `arena_go_daily_trade_limit`;
- `portfolio_snapshot_ttl_seconds`;
- `min_expected_edge_after_cost_score`.

Profitability Guardrails v1 defaults currently in Risk Control code:

- `opposite_action_cooldown_seconds = 900`;
- `reentry_after_close_cooldown_seconds = 1200`;
- `max_trades_per_instrument_per_hour = 2`;
- `max_trades_per_instrument_per_day = 6`;
- `base_min_edge_after_cost_bps = 30`;
- `reversal_min_edge_after_cost_bps = 50`;
- `quarantine_min_edge_after_cost_bps = 70`;
- `daily_soft_loss_pct = -0.10`;
- `daily_hard_loss_pct = -0.15`.

TODO before a larger live run: expose guardrail overrides through a governed risk-policy profile instead of editing code. Do not weaken these values during smoke.

## Recommended Smoke Parameters

Target profile name for notes/audit: `arenago_live_smoke_guarded`.

Use:

- `SYSTEM_MODE=automatic_live_trading`;
- `RUN_MODE=live_trading`;
- `ARENA_GO_SANDBOX=true`;
- `SAFE_LIVE_SUBMIT=true` only for the actual guarded smoke;
- `LIVE_READINESS_PASSED=true` only after startup preflight has passed;
- `SINGLE_SCHEDULER_INSTANCE=true`;
- duration: 30-60 minutes, manual stop;
- max total orders: 10-15 target, monitored through ArenaGo and analytics;
- max trades per instrument per hour: existing guardrail `2`;
- max trades per instrument per day: existing guardrail `6`;
- position size target: prefer DB limits around `max_position_pct = 0.01-0.02` and low `max_order_value_rub`;
- daily soft loss target: `-0.05%`;
- daily hard loss target: `-0.10%`.

If the DB currently has larger live limits, do not assume the env file can override them. Treat this as a pre-smoke blocker and either run paper/shadow or prepare a governed smoke risk-policy migration separately.

## Universe Recommendation

Do not use the full universe for the first smoke.

Recommended smoke universe:

- 2-4 liquid instruments;
- temporarily avoid or watchlist/quarantine: `VTBR`, `AFLT`, `GMKN`;
- candidates should have fresh market data, low spread/slippage, and no recent toxic analytics signal.

Current project supports `SELECTED_UNIVERSE_ID`, but this task does not create a new smoke universe. If `moex_top20_manual` is the only active universe, manually confirm the active instrument set before live. A dedicated `moex_smoke_guarded` universe should be added later through governance/registry SQL, not ad hoc during live.

## Before Start Checklist

- [ ] Git commit is done.
- [ ] Docker image rebuilt.
- [ ] Migrations/views applied.
- [ ] Preflight risk-policy report has no unresolved `HIGH` warnings.
- [ ] `RUN_MODE=live_trading` is explicit.
- [ ] This is the ArenaGo test portfolio, not an unintended portfolio.
- [ ] Open positions checked in ArenaGo.
- [ ] Max position size is small.
- [ ] Daily hard loss is enabled and stricter than normal.
- [ ] Kill switch / stop command is available.
- [ ] Decision/Risk/Execution logs are visible.
- [ ] `analytics.live_dashboard_summary` works.
- [ ] `sql/analytics/034_controlled_paper_validation_report.sql` is available from host.
- [ ] `portfolio_id` / `run_mode` scope verified in analytics.
- [ ] You know the exact command to stop the scheduler.

## Preflight: Check Active DB Risk Limits

Before guarded live smoke, run the read-only preflight report against the active database. It does not change risk policy or any runtime state.

PowerShell host pipe:

```powershell
Get-Content -Raw .\sql\analytics\035_live_smoke_preflight_report.sql |
  docker compose -f docker/docker-compose.prod.yml exec -T postgres_local `
    psql -U moex_agent -d moex_agent -P pager=off
```

Do not start live smoke if the report shows unresolved `HIGH` or material `WARNING` rows for daily loss, universe size, open positions, kill switch state, or max position size.

Preflight PASS:

- daily hard loss is set and not wider than `-0.10%`;
- `max_position_pct <= 0.02` for smoke, or order size is clearly bounded elsewhere by a governed limit;
- universe is small, or every active instrument is manually confirmed;
- `VTBR`, `AFLT`, and `GMKN` are not in the smoke universe, or are explicitly watchlisted/quarantined;
- kill switch / emergency stop path is available and policy state is known;
- latest portfolio snapshot is fresh;
- open positions are zero or manually understood;
- `RUN_MODE`, `SAFE_LIVE_SUBMIT`, `LIVE_READINESS_PASSED`, portfolio, and bot names are explicitly confirmed.

Preflight FAIL:

- daily hard loss is missing;
- `max_position_pct` is wide for smoke;
- full production universe is active without manual confirmation;
- kill switch state is unknown;
- latest portfolio snapshot is stale;
- open positions exist and are not understood;
- toxic instruments are active without quarantine/watchlist rationale;
- `SAFE_LIVE_SUBMIT=true` is planned before a dry-run check.

## Safer Dry Run Before Live Submit

Use this before `SAFE_LIVE_SUBMIT=true`:

```powershell
docker compose -f docker/docker-compose.prod.yml up -d --build
docker compose -f docker/docker-compose.prod.yml logs --tail=100 scheduler_worker
docker compose -f docker/docker-compose.prod.yml exec scheduler_worker python -c "import os; keys=['SYSTEM_MODE','RUN_MODE','ARENA_GO_SANDBOX','SAFE_LIVE_SUBMIT','LIVE_READINESS_PASSED','ARENA_GO_PORTFOLIO','ARENA_GO_BOT_NAME']; [print(k+'='+str(os.getenv(k))) for k in keys]"
```

Expected dry-run gate:

- `RUN_MODE=live_trading`;
- `ARENA_GO_SANDBOX=true`;
- `SAFE_LIVE_SUBMIT=false` until the final smoke start;
- portfolio/bot names match ArenaGo.

## Start Guarded Smoke

Only after the checklist passes and the intended env is in `config/api_keys.local.env`:

```powershell
docker compose -f docker/docker-compose.prod.yml up -d --build
docker compose -f docker/docker-compose.prod.yml logs -f scheduler_worker
```

Keep this test manually bounded. Stop after 30-60 minutes:

```powershell
docker compose -f docker/docker-compose.prod.yml stop scheduler_worker
```

Emergency stop:

```powershell
docker compose -f docker/docker-compose.prod.yml stop scheduler_worker
```

If needed, stop all project services:

```powershell
docker compose -f docker/docker-compose.prod.yml down
```

## During Test Monitoring

Every 10-15 minutes check:

```sql
SELECT * FROM analytics.live_dashboard_summary;
```

```sql
SELECT * FROM analytics.guardrail_rejections ORDER BY rejected_at DESC LIMIT 50;
```

```sql
SELECT * FROM analytics.churn_round_trips ORDER BY second_ts DESC LIMIT 50;
```

```sql
SELECT * FROM analytics.instrument_live_stats ORDER BY status_hint, avg_action_aligned_return_30m ASC NULLS LAST;
```

Also monitor:

- scheduler logs;
- Risk Control logs;
- Execution Engine logs;
- ArenaGo UI: open positions and today's trades.

## Stop Conditions

Stop immediately if any condition appears:

- `daily_pnl_pct <= -0.001` (`-0.10%`);
- any flip-flop within 15 minutes appears;
- more than 2 trades in one instrument within one hour;
- `guardrail_rejection_rate = 0` while the agent is actively trading;
- turnover grows while `avg_action_aligned_return_30m_bps` is negative;
- unexpected order bypass;
- kill switch / stop command is unavailable;
- Execution/ArenaGo gateway errors repeat;
- ArenaGo UI shows positions/trades that do not match expected portfolio.

## SQL Report Commands

The SQL report is not mounted inside `postgres_local` by default. Use one of these working host-side options.

Preferred PowerShell pipe:

```powershell
Get-Content -Raw .\sql\analytics\034_controlled_paper_validation_report.sql |
  docker compose -f docker/docker-compose.prod.yml exec -T postgres_local `
    psql -U moex_agent -d moex_agent -P pager=off
```

Copy into container, then run:

```powershell
$pg = docker compose -f docker/docker-compose.prod.yml ps -q postgres_local
docker cp .\sql\analytics\034_controlled_paper_validation_report.sql ${pg}:/tmp/controlled_paper_validation_report.sql
docker compose -f docker/docker-compose.prod.yml exec -T postgres_local `
  psql -U moex_agent -d moex_agent -P pager=off `
  -f /tmp/controlled_paper_validation_report.sql
```

If local `psql` is installed and Postgres is exposed to the host, you can run from host. The default prod compose does not publish port `5432`, so this is optional and environment-specific.

## After Test

Run the full report:

```powershell
Get-Content -Raw .\sql\analytics\034_controlled_paper_validation_report.sql |
  docker compose -f docker/docker-compose.prod.yml exec -T postgres_local `
    psql -U moex_agent -d moex_agent -P pager=off
```

Save:

- start/end timestamp;
- `analytics.live_dashboard_summary`;
- `analytics.instrument_live_stats`;
- `analytics.churn_round_trips`;
- `analytics.guardrail_rejections`;
- `analytics.edge_calibration`;
- `analytics.pnl_by_reason_code`;
- ArenaGo open positions and today's trades screenshot/export.

Compare with baseline:

- turnover;
- PnL;
- buy accuracy;
- churn count;
- toxic instruments.

Do not move to weights:v3 or a larger live run without analyzing the analytics report.

## PASS / FAIL

PASS for smoke means:

- no unexpected Execution Engine bypass;
- no daily hard stop;
- zero or near-zero flip-flop trades;
- guardrails reject weak/churn attempts when they happen;
- turnover remains small and controlled;
- ArenaGo portfolio/trades match expected scope.

FAIL means:

- churn appears;
- turnover rises with negative outcome;
- guardrail rejection rate is zero during active trading;
- toxic instruments reappear;
- expected positive post-cost edge realizes negative repeatedly;
- any gateway/execution mismatch occurs.

## Next Steps

If PASS:

- analyze report;
- repeat paper/shadow or a second tiny guarded smoke;
- only then prepare weights:v3 research as draft evidence.

If FAIL:

- stop live;
- return to paper/shadow;
- tighten or govern risk-policy limits;
- investigate analytics before any weight work.
