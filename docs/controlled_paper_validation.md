# Controlled Paper Validation

Цель этого runbook: проверить, что Profitability Guardrails v1, post-cost economics и analytics views улучшают поведение агента до любого guarded live запуска. Это не инструкция для включения live trading.

## Почему не сразу live

Последний технический тест показал рабочий execution path, но слабое торговое качество:

- overtrading и churn;
- частые flip-flop сделки;
- buy accuracy около 23%;
- avg buy forward return около -0.49% за 30 минут;
- turnover около 411 916 RUB;
- equity около -0.10%;
- проблемные инструменты: VTBR, AFLT, GMKN, ROSN.

После этого были добавлены guardrails, post-cost economics и analytics views. Их нужно проверить в controlled paper/shadow режиме: сначала доказать, что плохие сделки отсекаются, churn падает, а `expected_edge_after_cost_bps` хотя бы приблизительно калиброван против realized outcome.

## Режимы теста

### Stage A: analysis_only

Длительность: 30-60 минут.

Цель:

- проверить качество `decision_set` без order intents;
- убедиться, что analytics views заполняются и не смешивают scope;
- посмотреть `expected_edge_after_cost_bps`, reason codes, edge calibration.

Условия:

- `SYSTEM_MODE=analysis_only`;
- `RUN_MODE=paper_trading` или безопасный analysis profile проекта;
- live submit выключен;
- без ручного market-session override, если проверяется реальный календарь.

### Stage B: paper_trading

Длительность: 1-2 часа.

Цель:

- проверить Risk Control + Profitability Guardrails;
- проверить создание order intents и paper/shadow execution path;
- убедиться, что weak edge, churn и turnover-only сделки режутся до execution.

Условия:

- `RUN_MODE=paper_trading`;
- live submit выключен;
- маленький target size;
- видимые логи scheduler/risk/execution;
- portfolio/run_mode scope проверен в analytics.

### Stage C: guarded live

Только после PASS Stage A и Stage B.

Условия:

- минимальный размер;
- daily hard loss ниже обычного;
- ручной мониторинг;
- готовый kill switch;
- stop condition заранее записан.

## Safety checklist перед paper test

- [ ] live trading disabled;
- [ ] `RUN_MODE=paper_trading` или `SYSTEM_MODE=analysis_only`;
- [ ] текущие открытые позиции проверены;
- [ ] Docker image/container rebuilt;
- [ ] migrations applied;
- [ ] analytics views существуют;
- [ ] kill switch доступен;
- [ ] daily hard loss включен;
- [ ] logs видны;
- [ ] ArenaGo/broker mode verified;
- [ ] `portfolio_id` / `run_mode` scope verified.

## SQL report

Основной read-only отчет:

```powershell
docker compose -f docker/docker-compose.prod.yml exec -T postgres_local `
  psql -U moex_agent -d moex_agent -P pager=off `
  -f /path/to/sql/analytics/034_controlled_paper_validation_report.sql
```

Если файл не смонтирован в контейнер, выполняй блоки из `sql/analytics/034_controlled_paper_validation_report.sql` вручную через `psql -c`.

Ключевые views:

- `analytics.live_dashboard_summary`;
- `analytics.instrument_live_stats`;
- `analytics.churn_round_trips`;
- `analytics.guardrail_rejections`;
- `analytics.edge_calibration`;
- `analytics.pnl_by_reason_code`;
- `analytics.execution_cost_realized`;
- `analytics.feature_outcome_attribution`.

## PASS criteria

Минимальный PASS:

- flip-flop trades within 15m резко снизились или близки к 0;
- `buy_accuracy_30m > 40%`;
- `avg_buy_return_30m_bps >= 0` или сильно лучше предыдущего baseline;
- `avg_action_aligned_return_30m_bps >= 0`;
- `rejected_low_expected_edge_after_cost` появляется в `analytics.guardrail_rejections`;
- `rejected_anti_churn_opposite_action` появляется, если агент пытался flip-flop;
- turnover не растет без улучшения outcome;
- `edge_calibration_error_30m_bps` не систематически сильно отрицательный;
- daily hard stop не срабатывает в нормальных условиях;
- нет признаков Execution Engine bypass.

## FAIL criteria

FAIL, если:

- `analytics.churn_round_trips` всё еще высокий;
- hold всё еще лучше buy/sell;
- `buy_accuracy_30m < 35%`;
- `avg_buy_return_30m_bps < 0`;
- `guardrail_rejection_rate` почти 0 при плохом outcome;
- `expected_edge_after_cost_bps` положительный, но realized outcome стабильно отрицательный;
- turnover высокий, PnL отрицательный;
- токсичные инструменты не попадают в `watchlist_candidate` / `quarantine_candidate`;
- daily loss breaker не срабатывает при достижении лимита.

## After-test checklist

Зафиксировать:

- timestamp начала и конца теста;
- `analytics.live_dashboard_summary`;
- `analytics.instrument_live_stats`;
- `analytics.churn_round_trips`;
- `analytics.guardrail_rejections`;
- `analytics.edge_calibration`;
- `analytics.pnl_by_reason_code`;
- предыдущий baseline: turnover, PnL, buy accuracy, churn count.

Сравнить с baseline:

- previous turnover;
- previous PnL;
- previous buy accuracy;
- previous churn count.

## Что делать по результатам

Если churn остался:

- увеличить cooldown;
- снизить decision frequency;
- повысить `reversal_min_edge_after_cost_bps`.

Если buy accuracy плохой:

- снижать intraday-return / recovery-ratio signals;
- повышать signal agreement;
- снижать turnover urgency.

Если guardrails режут почти всё:

- проверить scale `expected_edge_after_cost_bps`;
- проверить cost model defaults;
- проверить slippage/spread;
- не ослаблять guardrails без анализа.

Если edge calibration сильно отрицательный:

- `raw_expected_edge_bps` scale завышен;
- cost model занижен;
- directional signal weak.

Если есть toxic instruments:

- включить quarantine или hard block для конкретных instruments;
- проверить, нет ли instrument-specific data quality problem.

## Переход к weights:v3

К weights:v3 можно переходить только после controlled validation report, где Stage B показывает PASS или понятные причины FAIL. Результат должен стать validation evidence для draft weights, но active weights нельзя менять автоматически.

Минимум для weights:v3 research:

- не менее 1-2 часов paper/shadow данных после guardrails;
- видимое снижение churn;
- edge buckets показывают монотонность хотя бы грубо;
- токсичные instruments идентифицированы;
- `pnl_by_reason_code` и `feature_outcome_attribution` показывают, какие signals вредят.

## Когда откатываться или ужесточать guardrails

Откатиться в observation-only / reduce-only, если:

- daily hard loss triggered;
- churn не падает;
- execution cost realized сильно выше expected;
- expected positive edge системно убыточен;
- guardrails не ловят очевидно слабые сделки.

Ужесточать guardrails до weight work, если:

- turnover высокий без улучшения outcome;
- reversal trades остаются частыми;
- токсичные instruments повторяются;
- `guardrail_rejection_rate` низкий при плохом realized outcome.
