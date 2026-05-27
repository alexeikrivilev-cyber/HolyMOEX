# MOEX Hybrid AI Trading Agent — Technical Documentation

Версия: `1.3 pivo edition  
Назначение: единая техническая документация для сборки модульного гибридного AI/quant-агента для анализа и автономной торговли выбранной вселенной ликвидных акций MOEX.

Документация написана на русском языке. Все программные сущности фиксируются на английском: `module_name`, `object_type`, `field_name`, `metric_name`, `contour`, `status`, `horizon`, `storage_name`, `request_type`, `decision_action`.

## 1. Архитектурная позиция

Система не является `LLM trader`. LLM не принимает финальное торговое решение и не отправляет заявки. LLM используется только там, где есть текст, неоднозначность и смысловая классификация: новости, отчётность, корпоративные события, макро-комментарии, раскрытия эмитентов.

Финальное решение принимает `Decision Engine Module` на базе `Feature Store`, `Metric Weights DB`, `Portfolio State Store`, `Risk Policy Store` и текущего `market_state`. Исполнение выполняет только `Execution Engine Module` после утверждения `Risk Control Module`.

Все рабочие модули, кроме `Orchestration Module` и `External Request Gateway Module`, запускаются только через `Orchestration Module` и получают `module_job`. `Orchestration Module` является корневым системным сервисом и работает по биржевому календарю, торговым сессиям и service-contour событиям. `External Request Gateway Module` является сервисным интерфейсом для внешних запросов и принимает `external_request` от любого авторизованного модуля без обязательного `module_job`.

Все внешние запросы выполняются только через `External Request Gateway Module`. Никакой модуль, включая `Data Intake & Routing Module`, не обращается к MOEX, broker API, LLM API, news API или macro API напрямую.

## 2. Общий поток данных

```text
Orchestration Module
  -> External Request Gateway Module
  -> raw data stores
  -> analytical modules
  -> Data Quality Module
  -> Normalization & Feature Vector Module
  -> Feature Store
  -> Decision Engine Module
  -> Risk Control Module
  -> Execution Engine Module
  -> Portfolio State Module
  -> Monitoring & Audit Module
```

Для текстовых и событийных источников используется отдельная ветка:

```text
Orchestration Module
  -> Data Intake & Routing Module
  -> External Request Gateway Module
  -> Raw Text Store
  -> Event & News Intelligence Module
  -> Event Store
  -> Feature Store
```

## 3. Контуры обновления

| `contour` | Частота / триггер | Назначение |
|---|---:|---|
| `realtime_contour` | `1m-5m` | котировки, свечи, стакан, спред, сделки, intraday pressure |
| `intraday_contour` | `15m-60m` | новости, intraday regime, sector movement, event reaction |
| `global_contour` | `4h-8h` | макро-контекст, общий рынок, ставка, валюта, нефть, risk-on/risk-off |
| `daily_contour` | `after_market_close` | дневные признаки, beta, correlations, fundamentals refresh, validation |
| `event_contour` | `on_event` | отчётность, дивиденды, санкции, ЦБ, аномальный объём, гэп, halt |
| `decision_contour` | `on_feature_update / scheduled` | сбор `feature_vector`, расчёт решения, подготовка `decision_set` |
| `execution_contour` | `realtime / on_approved_order` | выставление, изменение, отмена заявок, обработка fills |
| `monitoring_contour` | `continuous / scheduled / on_error` | мониторинг здоровья системы, аудит запусков, ошибок, решений, заявок и внешних запросов |
| `research_contour` | `batch / scheduled` | backtest, feature validation, score calibration, model comparison |
| `service_contour` | `dependency_driven` | orchestration, gateway, logging, monitoring, storage maintenance |

## 4. Горизонты решений

Каждая метрика и каждый `feature_vector` должны принадлежать одному или нескольким горизонтам:

| `horizon` | Смысл |
|---|---|
| `intraday` | решения внутри дня, чувствительны к стакану, спреду, объёму, новостям |
| `swing` | решения от нескольких дней до недель, чувствительны к momentum, volatility, events, market context |
| `position` | более длинный горизонт, чувствителен к fundamentals, dividends, macro, valuation |

Запрещено смешивать краткосрочные и долгосрочные признаки без указания `horizon`. Например, `order_flow_imbalance` не должен попадать в `position`-решение без отдельного `weight_rule`.

## 5. Основные хранилища

| `storage_name` | Назначение | Основные записи |
|---|---|---|
| `Selected Instruments DB` | вручную выбранная вселенная до 20 акций | `instrument_profile`, `instrument_alias`, `instrument_mapping` |
| `Raw Market Data Store` | сырые свечи, сделки, стакан, индексы | `raw_candle`, `raw_trade`, `raw_orderbook`, `raw_index_value` |
| `MOEX ISS market intake` | per instrument/per board/per timeframe загрузка и dedup | `raw_market` natural keys |
| `Raw Text Store` | новости, раскрытия, отчёты, тексты ЦБ/эмитентов | `raw_text_item` |
| `Raw Macro Data Store` | ставка, ОФЗ, валюта, сырьё, индексы | `raw_macro_point` |
| `Event Store` | структурированные события | `structured_event`, `event_cluster`, `event_reaction` |
| `Feature Store` | нормализованные и сырые значения метрик | `feature_record`, `feature_vector` |
| `Metric Weights DB` | веса метрик для `Decision Engine Module` | `weights_profile`, `metric_weight_rule` |
| `Risk Policy Store` | лимиты и правила риска | `risk_policy`, `instrument_limit`, `portfolio_limit` |
| `Portfolio State Store` | портфель, позиции, кэш, PnL, exposure | `portfolio_snapshot`, `position_state` |
| `Decision Store` | решения и вклад признаков | `decision_record`, `decision_explanation` |
| `Order Store` | намерения, заявки, fill reports | `order_intent`, `order_status`, `fill_report` |
| `Request Log Store` | запросы во внешние API | `external_request`, `external_response`, `external_request_log` |
| `Audit Log Store` | запуски модулей, ошибки, overrides | `audit_record`, `module_job`, `module_job_result`, `module_run` |
| `Analytics Store` | read-only витрины для анализа стратегии, весов, LLM и исполнения | `trade_fact`, `performance_daily`, `decision_outcome`, `feature_contribution`, `llm_quality`, `risk_gate_effectiveness` |

## 6. Таксономия метрик

Все метрики должны иметь `metric_type`:

| `metric_type` | Описание |
|---|---|
| `raw_metric` | значение напрямую из данных или простой формулы: `return_1d`, `spread_bps` |
| `derived_metric` | производная формула из нескольких `raw_metric`: `excess_return_vs_market`, `amihud_illiquidity` |
| `model_score` | результат ML/LLM модели: `event_materiality_score`, `report_sentiment_score` |
| `composite_score` | агрегированный скор группы: `price_strength_score`, `liquidity_risk_score` |

`composite_score` нельзя реализовывать как “магическое поле”. Для каждого `composite_score` должна существовать версия формулы в `calculation_version` или версия модели в `model_version`.

## 7. Унифицированные объекты

### 7.1 `module_job`

`module_job` является обязательным входом для рабочих модулей системы: data, analytical, hybrid_llm, decision, risk, execution, portfolio, research и monitoring modules.

Исключения:

- `Orchestration Module` не требует входного `module_job`, так как сам создаёт `module_job` по биржевому календарю, расписанию, событиям и dependency graph.
- `External Request Gateway Module` не требует входного `module_job` для штатного обслуживания запросов, так как принимает `external_request` от модулей как сервисный интерфейс. Служебные health-check/replay операции gateway могут оформляться через `module_job`, но это не является обязательным для каждого внешнего запроса.

```json
{
  "job_id": "string",
  "module_name": "string",
  "contour": "string",
  "trigger_type": "scheduled | event | dependency | manual | replay",
  "universe_id": "string",
  "instrument_ids": ["string"],
  "horizons": ["intraday", "swing", "position"],
  "time_range": {
    "from_ts": "string",
    "to_ts": "string",
    "timezone": "UTC"
  },
  "input_refs": ["string"],
  "config_ref": "string",
  "run_mode": "analysis_only | paper_trading | live_trading | backtest | replay",
  "idempotency_key": "string",
  "priority": "low | normal | high | critical"
}
```

### 7.2 `module_job_result`

```json
{
  "job_id": "string",
  "module_name": "string",
  "status": "success | partial_success | skipped | failed",
  "started_at": "string",
  "finished_at": "string",
  "output_refs": ["string"],
  "warnings": ["string"],
  "errors": ["string"],
  "metrics_written": 0,
  "events_written": 0,
  "data_quality_score": 0.0
}
```

### 7.3 `external_request`

Все внешние вызовы идут через `External Request Gateway Module`.

```json
{
  "request_id": "string",
  "caller_module": "string",
  "provider": "moex_iss | moex_fast | arena_go | polza_ai | broker_api | news_api | issuer_disclosure | macro_api | internal_cache",
  "request_type": "market_data | orderbook | trades | instruments | orders | portfolio | text_search | text_fetch | llm_completion | macro_series | submit_order | get_trades | get_positions | get_bots",
  "universe_id": "string",
  "instrument_ids": ["string"],
  "payload": {},
  "cache_policy": {
    "use_cache": true,
    "max_age_seconds": 0,
    "write_cache": true
  },
  "timeout_ms": 0,
  "retry_policy": {
    "max_retries": 0,
    "backoff_ms": 0
  },
  "idempotency_key": "string"
}
```

### 7.4 `external_response`

```json
{
  "request_id": "string",
  "provider": "string",
  "status": "success | partial_success | failed | timeout | rate_limited",
  "data_ref": "string",
  "received_at": "string",
  "latency_ms": 0,
  "cost_units": 0.0,
  "cache_hit": false,
  "warnings": ["string"],
  "errors": ["string"],
  "provider_tracking_id": "string"
}
```

### 7.5 `feature_record`

```json
{
  "feature_id": "string",
  "instrument_id": "string",
  "metric_name": "string",
  "metric_group": "price | liquidity | volatility | market_context | fundamental | event | dividend | derivative | portfolio | data_quality",
  "metric_type": "raw_metric | derived_metric | model_score | composite_score",
  "raw_value": 0.0,
  "normalized_value": 0.0,
  "unit": "string",
  "horizon": "intraday | swing | position",
  "contour": "string",
  "timestamp": "string",
  "ttl_seconds": 0,
  "confidence_score": 0.0,
  "source_module": "string",
  "source_refs": ["string"],
  "calculation_version": "string",
  "quality_flags": ["string"]
}
```

### 7.6 `feature_vector`

```json
{
  "feature_vector_id": "string",
  "instrument_id": "string",
  "horizon": "intraday | swing | position",
  "as_of_ts": "string",
  "features": {
    "metric_name": {
      "normalized_value": 0.0,
      "confidence_score": 0.0,
      "ttl_status": "fresh | stale | expired",
      "source_feature_id": "string"
    }
  },
  "coverage_ratio": 0.0,
  "data_quality_score": 0.0,
  "build_version": "string"
}
```

### 7.7 `structured_event`

```json
{
  "event_id": "string",
  "instrument_ids": ["string"],
  "event_type": "earnings | dividend | corporate_action | macro | regulation | sanctions | management | sector | market_structure | other",
  "event_subtype": "string",
  "event_ts": "string",
  "detected_at": "string",
  "source_refs": ["string"],
  "relevance_score": 0.0,
  "materiality_score": 0.0,
  "novelty_score": 0.0,
  "surprise_score": 0.0,
  "sentiment_score": 0.0,
  "confidence_score": 0.0,
  "evidence": ["string"],
  "reason_codes": ["string"],
  "model_version": "string"
}
```

### 7.8 `decision_request`

```json
{
  "decision_request_id": "string",
  "universe_id": "string",
  "instrument_ids": ["string"],
  "horizon": "intraday | swing | position",
  "as_of_ts": "string",
  "feature_vector_refs": ["string"],
  "portfolio_state_ref": "string",
  "weights_profile_id": "string",
  "run_mode": "analysis_only | paper_trading | live_trading",
  "decision_mode": "normal | risk_off | reduce_only | manual_approval_required"
}
```

### 7.9 `decision_set`

```json
{
  "decision_set_id": "string",
  "decision_request_id": "string",
  "horizon": "intraday | swing | position",
  "decisions": [
    {
      "instrument_id": "string",
      "action": "buy | sell | hold | reduce | close | block",
      "target_position_pct": 0.0,
      "target_quantity": 0,
      "confidence_score": 0.0,
      "expected_edge_score": 0.0,
      "risk_score": 0.0,
      "primary_reason_codes": ["string"],
      "feature_contributions": {
        "metric_name": 0.0
      }
    }
  ],
  "calculation_version": "string"
}
```

### 7.10 `risk_check_result`

```json
{
  "risk_check_id": "string",
  "decision_set_id": "string",
  "status": "approved | approved_with_changes | rejected | manual_review_required",
  "approved_order_intents": ["string"],
  "rejected_decisions": ["string"],
  "risk_flags": ["string"],
  "adjustments": [
    {
      "instrument_id": "string",
      "field": "string",
      "old_value": 0.0,
      "new_value": 0.0,
      "reason_code": "string"
    }
  ]
}
```

### 7.11 `order_intent`

```json
{
  "order_intent_id": "string",
  "instrument_id": "string",
  "side": "buy | sell",
  "quantity": 0,
  "order_type": "limit | market | stop | stop_limit",
  "limit_price": 0.0,
  "time_in_force": "day | ioc | fok | gtc",
  "max_slippage_bps": 0.0,
  "execution_ttl_seconds": 0,
  "decision_set_id": "string",
  "risk_check_id": "string",
  "run_mode": "paper_trading | live_trading"
}
```

### 7.12 `execution_result`

```json
{
  "execution_result_id": "string",
  "order_intent_id": "string",
  "status": "submitted | partially_filled | filled | cancelled | rejected | expired | failed",
  "broker_order_id": "string",
  "submitted_at": "string",
  "last_update_at": "string",
  "filled_quantity": 0,
  "avg_fill_price": 0.0,
  "fees": 0.0,
  "slippage_bps": 0.0,
  "errors": ["string"]
}
```

## 8. Dependency Graph

`Orchestration Module` обязан хранить `module_dependency_graph`. Граф определяет, какие модули пересчитываются после изменения данных.

Минимальный граф:

```text
Selected Instruments DB
  -> Data Intake & Routing Module
  -> Event & News Intelligence Module
  -> Earnings & Dividend Intelligence Module
  -> Corporate Actions Adjustment Module

Raw Market Data Store
  -> Market Data Metrics Module
  -> Liquidity & Microstructure Module
  -> Volatility & Risk Metrics Module
  -> Derivatives & Positioning Module

Raw Macro Data Store
  -> Market Context Module
  required public series: CBR key rate, RUONIA, CBR FX official rates, CBR ZCYC/OFZ curve, FRED/EIA Brent/WTI
  required market series through Raw Market Data Store: MOEX ISS IMOEX, RTSI, RGBI, USD/RUB, CNY/RUB

Event Store
  -> Event & News Intelligence Module
  -> Earnings & Dividend Intelligence Module
  -> Market Context Module

Feature Store
  -> Normalization & Feature Vector Module
  -> Decision Engine Module
  -> Risk Control Module
  -> Execution Engine Module
  -> Portfolio State Module
```

Полный пересчёт всей системы допустим только в `research_contour`, `backtest` или при миграции `calculation_version`. В production-режиме используется инкрементальный пересчёт.

## 9. Взаимодействие модулей

Правила обязательны для всех модулей:

1. Рабочий модуль не запускается самостоятельно. Он принимает только `module_job`. Исключения: `Orchestration Module` работает как корневой scheduler/service, `External Request Gateway Module` работает как сервисный request gateway.
2. Модуль не вызывает другой аналитический модуль напрямую.
3. Модуль не обращается к внешнему API напрямую. Все внешние вызовы идут через `External Request Gateway Module`.
4. Модуль читает только разрешённые `input_refs` и stores.
5. Модуль пишет только в разрешённые output stores.
6. Каждый output должен иметь `timestamp`, `source_module`, `calculation_version`, `confidence_score`, `ttl_seconds`.
7. Любой `model_score` должен иметь `model_version`, `confidence_score`, `evidence` или `reason_codes`.
8. Любой `composite_score` должен иметь воспроизводимую формулу или ссылку на `calculation_version`.
9. Ошибка одного модуля не должна останавливать всю систему, кроме `critical_dependency`.
10. Все решения и заявки должны быть воспроизводимы через `Audit Log Store`.

## 10. Список модулей

| `module_name` | Русский перевод | `module_type` | Основной `contour` | Файл |
|---|---|---|---|---|
| `Orchestration Module` | Модуль оркестрации | service | `service_contour` | `modules/01_orchestration_module.md` |
| `External Request Gateway Module` | Шлюз внешних запросов | service | `service_contour` | `modules/02_external_request_gateway_module.md` |
| `Selected Instruments Registry Module` | Реестр выбранных инструментов | service/storage | `service_contour` | `modules/03_selected_instruments_registry_module.md` |
| `Data Intake & Routing Module` | Приём и маршрутизация текстовых данных | service | `intraday_contour`, `event_contour` | `modules/04_data_intake_routing_module.md` |
| `Data Quality Module` | Контроль качества данных | service | `service_contour` | `modules/05_data_quality_module.md` |
| `Market Data Metrics Module` | Рыночные ценовые метрики | analytical | `realtime_contour`, `daily_contour` | `modules/06_market_data_metrics_module.md` |
| `Liquidity & Microstructure Module` | Ликвидность и микроструктура | analytical | `realtime_contour` | `modules/07_liquidity_microstructure_module.md` |
| `Volatility & Risk Metrics Module` | Волатильность и рыночный риск | analytical | `realtime_contour`, `daily_contour` | `modules/08_volatility_risk_metrics_module.md` |
| `Market Context Module` | Макро, сектор и режим рынка | analytical | `global_contour`, `daily_contour` | `modules/09_market_context_module.md` |
| `Fundamental & Valuation Module` | Фундаментал и оценка | analytical | `daily_contour`, `event_contour` | `modules/10_fundamental_valuation_module.md` |
| `Event & News Intelligence Module` | Новости и события | hybrid_llm | `intraday_contour`, `event_contour` | `modules/11_event_news_intelligence_module.md` |
| `Earnings & Dividend Intelligence Module` | Отчётность и дивиденды | hybrid_llm | `event_contour`, `daily_contour` | `modules/12_earnings_dividend_intelligence_module.md` |
| `Corporate Actions Adjustment Module` | Корпоративные действия и корректировки | analytical | `event_contour`, `daily_contour` | `modules/13_corporate_actions_adjustment_module.md` |
| `Derivatives & Positioning Module` | Деривативы и позиционирование | analytical | `intraday_contour`, `daily_contour` | `modules/14_derivatives_positioning_module.md` |
| `Normalization & Feature Vector Module` | Нормализация и сбор feature-vector | analytical/service | `decision_contour` | `modules/15_normalization_feature_vector_module.md` |
| `Feature Validation & Research Module` | Проверка признаков и исследования | research | `research_contour` | `modules/16_feature_validation_research_module.md` |
| `Decision Engine Module` | Модуль принятия решений | decision | `decision_contour` | `modules/17_decision_engine_module.md` |
| `Risk Control Module` | Контроль риска | risk | `decision_contour`, `execution_contour` | `modules/18_risk_control_module.md` |
| `Execution Engine Module` | Исполнение сделок | execution | `execution_contour` | `modules/19_execution_engine_module.md` |
| `Portfolio State Module` | Состояние портфеля | service | `execution_contour`, `daily_contour` | `modules/20_portfolio_state_module.md` |
| `Backtesting & Paper Trading Module` | Бэктест и бумажная торговля | research/execution_sim | `research_contour` | `modules/21_backtesting_paper_trading_module.md` |
| `Monitoring & Audit Module` | Мониторинг и аудит | service | `monitoring_contour` | `modules/22_monitoring_audit_module.md` |

## 11. Selected Instruments DB

Вселенная системы ограничивается вручную выбранными инструментами. Базовое ограничение: до `20` активных акций.

`instrument_profile`:

```json
{
  "instrument_id": "string",
  "ticker": "string",
  "figi": "string",
  "isin": "string",
  "class_code": "string",
  "board_id": "string",
  "lot_size": 0,
  "min_price_increment": 0.0,
  "currency": "string",
  "sector": "string",
  "issuer_name": "string",
  "aliases": ["string"],
  "related_entities": ["string"],
  "is_active": true,
  "tradable": true,
  "allowed_horizons": ["intraday", "swing", "position"],
  "created_at": "string",
  "updated_at": "string"
}
```

Любой модуль обязан фильтровать работу по `universe_id` и `is_active=true`. Если входной текст или рыночные данные не сопоставлены с активным `instrument_id`, они не должны попадать в `Feature Store` как торговые признаки.

## 12. Decision Engine и Metric Weights DB

`Decision Engine Module` использует только версионированные веса. Вес не хранится в коде модуля.

Active paper/analysis baseline profiles:

| Horizon | `weights_profile_id` | Role |
|---|---|---|
| `intraday` | `weights:product_baseline:intraday:v1` | tactical price, liquidity pressure, event and risk gating |
| `swing` | `weights:product_baseline:swing:v1` | price, event, earnings/fundamental, market context and risk |
| `position` | `weights:product_baseline:position:v1` | fundamental, valuation, dividends, macro/sector context and risk |

The previous `strict_default` profiles are retained for audit/replay but seeded as `deprecated`. Runtime schedules for analysis/paper use `product_baseline` profiles. Autonomous live trading uses separate `weights:live_autonomous:*:v1` profiles and `risk_policy:live_autonomous_turnover:v1`, seeded by the autonomous live governance migration.

`weights_profile`:

```json
{
  "weights_profile_id": "string",
  "profile_name": "string",
  "version": "string",
  "status": "draft | active | deprecated | archived",
  "horizon": "intraday | swing | position",
  "run_mode_allowed": ["analysis_only", "paper_trading", "live_trading"],
  "created_at": "string",
  "approved_by": "string",
  "validation_report_ref": "string"
}
```

`metric_weight_rule`:

```json
{
  "metric_weight_rule_id": "string",
  "weights_profile_id": "string",
  "metric_name": "string",
  "metric_group": "string",
  "horizon": "intraday | swing | position",
  "instrument_scope": "all | sector | instrument",
  "instrument_ids": ["string"],
  "sector": "string",
  "weight": 0.0,
  "direction": "positive | negative | nonlinear",
  "transform": "identity | zscore | percentile | rank | clipped | custom",
  "min_confidence_score": 0.0,
  "stale_policy": "ignore | downweight | block_decision",
  "calculation_version": "string"
}
```

Изменение `active` профиля допустимо только через новый `version` и `validation_report_ref`. Автоматическое изменение весов из research-модуля запрещено без отдельного governance-процесса.

## 13. Execution safety rules

`Execution Engine Module` не имеет права отправлять заявку, если:

- `run_mode` не равен `live_trading` или `paper_trading`;
- нет `risk_check_result.status` со значением `approved` или `approved_with_changes`;
- `order_intent.execution_ttl_seconds` истёк;
- `market_session_status` не разрешает выставление заявок;
- `instrument_profile.tradable=false`;
- `data_quality_score` ниже минимального порога;
- `spread_bps` или `estimated_slippage_bps` выше лимита;
- `portfolio_state` устарел;
- включён `global_kill_switch`.

## 14. Минимальные acceptance criteria для сборки

Система считается собираемой, если выполнены условия:

1. Каждый рабочий модуль, кроме `Orchestration Module` и `External Request Gateway Module`, принимает `module_job` и возвращает `module_job_result`.
2. Каждый внешний запрос идёт через `External Request Gateway Module`.
3. Все программные поля на английском, в `snake_case`.
4. Все timestamps в UTC ISO-8601.
5. Каждый output имеет `source_module`, `calculation_version`, `timestamp`, `confidence_score`.
6. `Feature Store` хранит `raw_value` и `normalized_value` отдельно.
7. `Decision Engine Module` использует `Metric Weights DB`, а не зашитые веса.
8. `Execution Engine Module` не принимает решение, а исполняет только утверждённый `order_intent`.
9. `Risk Control Module` может заблокировать любое решение.
10. Любое решение можно воспроизвести по `decision_record`, `feature_vector`, `weights_profile_id`, `risk_policy` и `calculation_version`.

## 15. Источники и технологические ориентиры

- MOEX ISS предоставляет доступ к статическим рыночным данным, инструментам, свечам, сделкам, котировкам, историческим данным и metadata: `https://www.moex.com/a2920`, `https://www.moex.com/a8531`.
- MOEX ASTS Bridge и FAST/TWIME относятся к низколатентным интерфейсам прямого доступа и market data: `https://www.moex.com/a1523`, `https://www.moex.com/a7939`.
- T-Invest API описывается как gRPC API для торговых поручений, рыночных данных, портфеля и исторических котировок: `https://developer.tinkoff.ru/invest/intro/intro/`.
- Торговый календарь MOEX должен использоваться как внешний источник для `market_session_status`: `https://www.moex.com/en/tradingcalendar/`.


---

# Дополнение v2 — Docker, Local DB, ArenaGo, PolzaAI, secrets и строгая сборка

Версия дополнения: `2.0-strict-product`  
Статус: обязательная часть документации. Данное дополнение не заменяет предыдущие разделы, а расширяет их.

## 16. Runtime architecture

Финальная система собирается и запускается через `Docker`. База данных работает локально внутри Docker-среды. Внешние сервисы доступны только через `External Request Gateway Module`.

Минимальный runtime-состав:

| `runtime_service` | Назначение |
|---|---|
| `agent_app` | основной backend-контейнер с модулями системы |
| `postgres_local` | локальная БД в Docker для всех stores |
| `redis_local` | optional queue/cache для orchestration, locks, short cache |
| `scheduler_worker` | primary long-running daemon для scheduled jobs |
| `research_worker` | optional worker для research/backtest jobs |

Для строгой первой сборки допустимо держать `agent_app`, `scheduler_worker` и `research_worker` как один image с разными entrypoint. БД должна быть отдельным Docker service. `agent_app` и `research_worker` являются one-shot/manual профилями; основным long-running процессом является `scheduler_worker`.

### 16.1 Database policy

Основная БД: `postgres_local`. Рекомендуется `PostgreSQL 16+`. Для vector search можно использовать `pgvector`, но аналитические модули не должны зависеть от vector search как от критической зависимости.

Все stores из раздела 5 должны быть реализованы как schemas/tables внутри `postgres_local` либо как logical namespaces в одной БД:

| `store` | Recommended schema |
|---|---|
| `Selected Instruments DB` | `registry` |
| `Raw Market Data Store` | `raw_market` |
| `Raw Text Store` | `raw_text` |
| `Raw Macro Data Store` | `raw_macro` |
| `Event Store` | `events` |
| `Feature Store` | `features` |
| `Metric Weights DB` | `weights` |
| `Risk Policy Store` | `risk` |
| `Portfolio State Store` | `portfolio` |
| `Decision Store` | `decisions` |
| `Order Store` | `orders` |
| `Request Log Store` | `request_logs` |
| `Audit Log Store` | `audit` |
| `Analytics Store` | `analytics` |

Запрещено хранить ключевые trading state только в памяти контейнера. После рестарта должны восстанавливаться: `portfolio_snapshot`, `position_state`, `decision_record`, `order_intent`, `execution_result`, `external_request`, `external_response`, `external_request_log`, `module_job`, `module_job_result`, `module_run`, `feature_record`.

### 16.1.1 Database readiness contract

Миграции PostgreSQL находятся в `agent_app/storage/postgres/migrations` и применяются строго по имени файла. В `docker/docker-compose.example.yml` этот каталог монтируется в `/docker-entrypoint-initdb.d`, поэтому новая локальная база инициализируется схемой и governance seed-данными автоматически при первом старте volume.

После применения миграций базовый контроль готовности выполняется запросом:

```sql
SELECT *
  FROM audit.database_readiness_check
 ORDER BY check_name;
```

Public macro intake readiness is checked separately:

```sql
SELECT *
  FROM audit.public_macro_series_readiness_check
 ORDER BY check_name;
```

MOEX ISS raw-market idempotency is checked separately:

```sql
SELECT *
  FROM audit.moex_market_data_idempotency_check
 ORDER BY check_name;
```

Все строки должны иметь `status = 'pass'`. Этот view проверяет только инфраструктурную готовность БД: наличие stores, активного universe, стартового portfolio state, paper-trading risk policy, provider/source config, module schedules, dependency graph и product baseline seed-весов. Он не означает, что рыночные/новостные/макро данные уже заполнены владельцем.

Детальная проверка весов выполняется через:

```sql
SELECT *
  FROM audit.metric_weights_readiness_check
 ORDER BY check_name;
```

Для `Metric Weights DB` действует governance-ограничение: active seed-веса являются product baseline для `analysis_only` и `paper_trading`. Дальнейшая эмпирическая оптимизация весов должна сначала давать draft-предложение и validation report. Отдельный промпт для аналитической модели лежит в `prompts/metric_weights_optimization_prompt.md`; его результат нельзя автоматически активировать без ручного governance approval.

Публичный контур источников данных проверяется отдельно:

```sql
SELECT *
  FROM audit.public_data_source_readiness_check
 ORDER BY check_name;
```

Этот view проверяет, что включены gateway providers `news_api`, `issuer_disclosure`, `macro_api`, а конкретные публичные источники новостей, раскрытий и макро заведены в `raw_text.text_source_config`. Конкретные порталы/RSS не становятся новыми provider names: они задаются через `query_template.endpoint`, чтобы сохранить контракт Gateway.

Product-level нормализация `raw_text_item` проверяется через:

```sql
SELECT *
  FROM audit.public_text_intake_readiness_check
 ORDER BY check_name;
```

Каждый raw text item должен сохранять `source`, `source_url`, `published_at`, `fetched_at`, `title`, `body`, `language`, `trust_level`, `confidence_score`, `instrument_candidates`, `issuer_candidates` и `quality_flags`. Fast-news источники дают candidate/early signal; confirmed corporate event требует official confirmation layer.

### 16.2 Docker environment contract

Все конфигурации должны приходить из environment variables или mounted config files. Секреты не хранятся в markdown, коде или git.

Required env variables:

```env
APP_ENV=production
SYSTEM_MODE=automatic_live_trading
RUN_MODE=live_trading
DATABASE_URL=postgresql://moex_agent:moex_agent_password@postgres_local:5432/moex_agent
REDIS_URL=redis://redis_local:6379/0
SINGLE_SCHEDULER_INSTANCE=true
DEFAULT_TIMEZONE=Europe/Moscow
INITIAL_CAPITAL_RUB=1000000
SELECTED_UNIVERSE_ID=moex_top20_manual
MOEX_ISS_BASE_URL=https://iss.moex.com/iss
NEWS_API_BASE_URL=https://www.rbc.ru
ISSUER_DISCLOSURE_BASE_URL=https://www.e-disclosure.ru
MACRO_API_BASE_URL=https://www.cbr.ru
ARENA_GO_BASE_URL=https://arenago.ru/api
ARENA_GO_SANDBOX=true
SANDBOX_API_KEY=replace_with_arena_go_sandbox_token
ARENA_GO_TOKEN=
ARENA_GO_PORTFOLIO=
ARENA_GO_BOT_NAME=
ARENA_GO_DAILY_TRADE_LIMIT=1000
SAFE_LIVE_SUBMIT=false
LIVE_READINESS_PASSED=false
ARENA_GO_SHORTS_ALLOWED=true
DECISION_ALLOW_SHORT_SELLING=true
DECISION_USE_POST_COST_EDGE_FOR_ACTIONS=true
DECISION_PARTIAL_TAKE_PROFIT_ENABLED=true
DECISION_PARTIAL_TAKE_PROFIT_RATIO=0.5
DECISION_PROFIT_LOCK_ENABLED=true
DECISION_EXIT_USE_POST_COST_EDGE=true
DECISION_SHORT_ENTRY_THRESHOLD=0.012
DECISION_SHORT_ADD_THRESHOLD=0.018
DECISION_SHORT_USE_POST_COST_EDGE=true
DECISION_ALLOW_LONG_TO_SHORT_FLIP=false
DECISION_SHORT_PARTIAL_TAKE_PROFIT_RATIO=0.5
DECISION_SHORT_PROFIT_LOCK_ENABLED=true
POLZA_BASE_URL=https://polza.ai/api/v1
POLZA_API_KEY=replace_with_real_key
POLZA_FAST_MODEL=deepseek/deepseek-v4-flash
POLZA_REASONING_MODEL=qwen/qwen3.6-35b-a3b
POLZA_DEFAULT_MODEL=qwen/qwen3.6-35b-a3b
POLZA_LLM_MODEL=
LLM_MAX_ITEMS_PER_RUN=5
LLM_MAX_CALLS_PER_MINUTE=6
LLM_MAX_CALLS_PER_HOUR=120
EVENT_NEWS_FAST_INTERVAL_SECONDS=120
EVENT_NEWS_MAX_ITEMS_PER_RUN=5
EVENT_NEWS_FAST_FIRST=true
EVENT_NEWS_REASONING_ESCALATION_ENABLED=true
LLM_MIN_SECONDS_BETWEEN_CALLS=2
ENABLE_LLM_TEXT_SCHEDULES=true
MARKET_DATA_FETCH_RAW_TRADES=false
LIQUIDITY_FETCH_RAW_TRADES=false
PIPELINE_LOOKBACK_MINUTES=240
ARENA_GO_EXTENDED_LOOKBACK_MINUTES=480
LLM_DEFAULT_TEMPERATURE=0
LLM_DEFAULT_RESPONSE_FORMAT=json_object
```

Файл с ключами должен быть отдельным локальным файлом: `config/api_keys.local.env`. В репозитории хранится только шаблон: `config/api_keys.example.env`.

### 16.3 Local test commands

Dev/test dependencies are isolated in `requirements-dev.txt`:

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall -q agent_app tests
python -m unittest discover -s tests -p "test*.py" -v
python -m pytest -q
```

## 17. ArenaGo execution integration

Торговая платформа: `ArenaGo`. Начальный капитал системы: `1000000 RUB`. Целевой продуктовый режим: автономный `live_trading` с отдельным live risk policy, live weights и turnover mandate. `paper_trading` остаётся обязательным проверочным контуром, но не является финальным режимом продукта.

### 17.1 ArenaGo provider

`External Request Gateway Module` должен поддерживать provider:

```json
{
  "provider": "arena_go",
  "base_url_env": "ARENA_GO_BASE_URL",
  "auth_header": "Authorization",
  "auth_value_source": "SANDBOX_API_KEY",
  "auth_fallback_value_sources": ["ARENA_GO_TOKEN"],
  "portfolio_env": "ARENA_GO_PORTFOLIO",
  "bot_name_env": "ARENA_GO_BOT_NAME"
}
```

ArenaGo API используется только через `External Request Gateway Module`. `Execution Engine Module` не имеет права напрямую выполнять HTTP-запросы.

### 17.2 Submit order contract

Gateway request:

```json
{
  "request_id": "string",
  "caller_module": "Execution Engine Module",
  "provider": "arena_go",
  "request_type": "submit_order",
  "payload": {
    "direction": "B | S",
    "secid": "string",
    "quantity": 0,
    "bot": "string"
  },
  "idempotency_key": "string"
}
```

ArenaGo HTTP request:

```http
POST /submit_order
Content-Type: application/json
Authorization: ${SANDBOX_API_KEY}
```

```json
{
  "direction": "B",
  "secid": "SBER",
  "quantity": 10,
  "bot": "exact bots[].name from /api/bots"
}
```

Normalized gateway response:

```json
{
  "provider": "arena_go",
  "request_type": "submit_order",
  "status": "success | failed",
  "data": {
    "success": true,
    "message": "string",
    "order_value": 0.0,
    "price": 0.0,
    "quantity": 0,
    "remaining_cash": 0.0
  },
  "errors": []
}
```

ArenaGo error mapping:

| ArenaGo error | Normalized `error_code` | Required reaction |
|---|---|---|
| `ERROR: MARKET CLOSED` | `market_closed` | reject execution, set `market_session_status=closed` |
| `ERROR: NOT VALID SECID` | `invalid_instrument` | disable instrument for execution until registry check |
| `ERROR: INSUFFICIENT CASH` | `insufficient_cash` | refresh portfolio, reject or reduce order |
| `ERROR: BOT {bot_name} HAS REACHED DAILY TRADE LIMIT` | `daily_trade_limit_reached` | enable `execution_kill_switch` for bot until next trading day |

Important: internal `order_intent.quantity` and portfolio positions are stored in shares. For the observed ArenaGo sandbox API, `submit_order.quantity` is submitted in lots by default (`ARENA_GO_SUBMIT_QUANTITY_UNITS=lots`), so `Execution Engine Module` converts share targets through `lot_size` and `arena_go_quantity_mode`. Override to `shares` only after a broker-side contract check proves the API expects shares for the active contour.

### 17.3 Trades, positions and bots

Gateway request types:

| `request_type` | HTTP endpoint | Consumer module |
|---|---|---|
| `get_trades` | `GET /trades/{portfolio}` | `Portfolio State Module`, `Monitoring & Audit Module` |
| `get_positions` | `GET /positions/{portfolio}` | `Portfolio State Module`, `Risk Control Module` |
| `get_bots` | `GET /bots` | `Portfolio State Module`, `Monitoring & Audit Module` |

Normalized `arena_go_trade`:

```json
{
  "tradedate": "string",
  "tradetime": "string",
  "direction": "B | S",
  "secid": "string",
  "quantity": 0,
  "price": 0.0,
  "bot": "string"
}
```

Normalized `arena_go_position`:

```json
{
  "secid": "string",
  "position": 0,
  "average_price": 0.0,
  "bot": "string"
}
```

Normalized `arena_go_bot`:

```json
{
  "name": "string",
  "cash_balance": 0.0
}
```

## 18. PolzaAI LLM integration

LLM-провайдер: `PolzaAI`. Task-specific routing имеет приоритет: лёгкие новости/классификация используют `POLZA_FAST_MODEL=deepseek/deepseek-v4-flash`, сложные отчёты/макро/reasoning используют `POLZA_REASONING_MODEL=qwen/qwen3.6-35b-a3b`, а `POLZA_DEFAULT_MODEL` служит общим fallback. `POLZA_LLM_MODEL` оставлен только для совместимости. Текущий gateway реализует strict JSON `llm_completion` smoke и `GET /models` availability-check через `provider=polza_ai`, `request_type=models`. В deploy-check `GET /models` используется как основной healthcheck, а strict JSON completion остаётся fallback-проверкой провайдера.

PolzaAI вызывается только через `External Request Gateway Module`. LLM-модули не имеют права напрямую создавать HTTP-клиент к PolzaAI.

Gateway request для LLM:

```json
{
  "request_id": "string",
  "caller_module": "Event & News Intelligence Module",
  "provider": "polza_ai",
  "request_type": "llm_completion",
  "payload": {
    "model": "deepseek/deepseek-v4-flash",
    "task_type": "event_extraction",
    "messages": [],
    "temperature": 0,
    "response_format": {"type": "json_object"},
    "max_completion_tokens": 2000,
    "reasoning": {
      "enabled": true,
      "effort": "medium",
      "summary": "auto"
    }
  },
  "cache_policy": {
    "use_cache": true,
    "max_age_seconds": 3600,
    "write_cache": true
  }
}
```

LLM output must be parsed into strict JSON. Free-form text from LLM is not accepted as final module output.

Required LLM output envelope:

```json
{
  "schema_version": "string",
  "model_id": "string",
  "model_version": "string",
  "task_type": "event_extraction | sentiment_scoring | report_extraction | dividend_extraction | macro_text_analysis",
  "instrument_ids": ["string"],
  "items": [],
  "confidence_score": 0.0,
  "evidence": [],
  "reason_codes": [],
  "warnings": []
}
```

Запрещено использовать LLM-output для отправки заявок, изменения весов, изменения risk policy или прямой записи в `Order Store`.

## 19. Strict module boundary policy

Каждый модуль обязан иметь раздел `Forbidden actions`. Эти запреты являются частью acceptance criteria.

Общие запреты для всех модулей:

1. Рабочим модулям запрещено запускаться без `module_job`. Исключения: `Orchestration Module` запускается по биржевому календарю и service schedule, `External Request Gateway Module` принимает `external_request` как сервисный интерфейс.
2. Запрещено делать прямой HTTP-запрос к внешнему API, минуя `External Request Gateway Module`.
3. Запрещено писать в stores, которые не указаны в спецификации модуля.
4. Запрещено менять `Metric Weights DB`, если модуль не является governance-approved writer.
5. Запрещено менять `Risk Policy Store`, если модуль не является governance-approved writer.
6. Запрещено отправлять заявки, если модуль не `Execution Engine Module`.
7. Запрещено принимать торговое решение, если модуль не `Decision Engine Module`.
8. Запрещено скрывать ошибки: каждая ошибка должна попадать в `Audit Log Store`.
9. Запрещено записывать feature без `calculation_version`.
10. Запрещено использовать stale/expired feature без явной `stale_policy`.

## 20. Strict formula policy

Каждая метрика в модульных спецификациях должна иметь формулу или строгое правило расчёта. Если метрика является `model_score`, формулой считается schema + шкала + критерии scoring + `model_version`. Если метрика является `composite_score`, должна быть указана агрегирующая формула через нормализованные признаки.

Base notation:

| Symbol | Meaning |
|---|---|
| `P_t` | close price at timestamp `t` |
| `O_t` | open price |
| `H_t` | high price |
| `L_t` | low price |
| `V_t` | traded volume in shares |
| `Turnover_t` | traded value in RUB |
| `Ret_n` | return over `n` periods |
| `Z(x, w)` | rolling z-score of `x` over window `w` |
| `PctRank(x, w)` | rolling percentile rank of `x` over window `w` |
| `RankSector(x)` | cross-sectional rank inside sector |
| `RankMarket(x)` | cross-sectional rank inside selected universe |
| `Clip(x, a, b)` | clipped value between `a` and `b` |
| `WAvg(values, weights)` | weighted average using active `Metric Weights DB` profile |

## 21. Updated assembly checklist

Перед финальной сборкой разработчики должны проверить:

1. `docker-compose.yml` поднимает `agent_app`, `postgres_local`, optional `redis_local`.
2. `config/api_keys.local.env` существует локально и не попадает в git.
3. `INITIAL_CAPITAL_RUB=1000000` записан в initial `portfolio_snapshot`.
4. `ArenaGo provider` реализован в `External Request Gateway Module`.
5. `PolzaAI provider` реализован в `External Request Gateway Module`.
6. Все LLM-запросы возвращают JSON по утверждённой schema.
7. Все метрики из модульных файлов имеют formula/rule.
8. Все модули имеют `Forbidden actions`.
9. Все ArenaGo ошибки мапятся в normalized `error_code`.
10. `Risk Control Module` проверяет daily trade limit, cash, exposure, stale portfolio, market session.
11. `Execution Engine Module` не выполняет заявку без approved `risk_check_result`.
12. `Portfolio State Module` синхронизирует cash/positions/trades через ArenaGo перед live decision.
13. PostgreSQL migrations are applied by `python -m agent_app.storage.postgres.apply_migrations` before application services start; fresh volumes and existing volumes must both pass migration readiness.
14. `audit.database_readiness_check` returns only `status = 'pass'` rows before runtime assembly.
15. `audit.metric_weights_readiness_check` returns only `status = 'pass'` rows and Decision schedule references `weights:product_baseline:*:v1`.
16. Any future empirically optimized `Metric Weights DB` proposal starts from `prompts/metric_weights_optimization_prompt.md` and remains `draft` until manual governance approval.

---

# Дополнение v3 — Autonomous Live Trading, turnover mandate и server runtime

Версия дополнения: `3.0-autonomous-live-turnover`
Статус: обязательная часть документации. Это дополнение уточняет, что целевой продуктовый режим системы — не advisory и не paper-only, а полностью автономный `live_trading` агент с автоматическими risk gates.

## 22. Product goal

Целевой режим системы: `fully_autonomous_live_trading`.

Агент должен сам выполнять полный контур:

```text
persistent scheduler
  -> public/open data refresh
  -> portfolio sync from ArenaGo
  -> feature refresh
  -> decision_set generation
  -> risk_check_result
  -> approved order_intents
  -> ArenaGo execution
  -> fills/trades/positions sync
  -> monitoring/audit/alerts
```

Ручное подтверждение каждой нормальной сделки не требуется. `manual_review_required` используется только для abnormal cases: конфликт источников, stale portfolio, market closed, invalid secid, превышение лимитов, низкое качество данных, kill switch, подозрительная цена, высокий spread/slippage или иная критическая неоднозначность.

## 23. Turnover mandate

Для live-режима вводится обязательный trading mandate:

```json
{
  "trading_mandate_id": "trading_mandate:live:turnover_10m_14d:v1",
  "run_mode": "live_trading",
  "initial_capital_rub": 1000000,
  "target_gross_turnover_rub": 10000000,
  "target_window_days": 14,
  "target_turnover_ratio": 10.0,
  "objective_priority": "secondary_after_risk_and_positive_expected_edge"
}
```

Формула оборота:

```text
gross_turnover_rub = sum(abs(filled_quantity * avg_fill_price))
```

Считать оборот нужно по фактическим `fill_report`, `execution_result` и `ArenaGo get_trades`, а не по `order_intent`.

Turnover mandate делает агента активным, но не разрешает бессмысленный churn. Сделки ради оборота допускаются только среди решений с положительным `expected_edge_after_costs`, достаточным `confidence_score`, нормальной ликвидностью и прохождением `Risk Control Module`.

## 24. Module responsibility for turnover mandate

| Responsibility | Module / Store |
|---|---|
| Mandate definition | `risk.trading_mandate`, `Risk Policy Store` |
| Turnover progress calculation | `Portfolio State Module` |
| Turnover-aware decision urgency | `Decision Engine Module` |
| Safety limits and anti-churn gates | `Risk Control Module` |
| Real execution only after approved risk | `Execution Engine Module` |
| Progress, lag and harmful churn monitoring | `Monitoring & Audit Module` |
| Feasibility validation | `Backtesting & Paper Trading Module`, `Feature Validation & Research Module` |

`Portfolio State Module` writes portfolio payload fields:

```text
gross_turnover_rub_1d
gross_turnover_rub_14d
turnover_ratio_14d
turnover_progress_ratio
target_completion_pct
remaining_turnover_rub_14d
required_daily_turnover_rub
projected_turnover_rub_14d
turnover_target_status
```

`Decision Engine Module` may increase trade urgency when `turnover_target_status` is `behind` or `critically_behind`, but it must not convert negative-edge trades into buys just to hit turnover.

`Risk Control Module` enforces:

```text
max_single_order_value_rub
max_trade_count_per_day
max_position_pct
max_gross_exposure_pct
max_daily_loss_pct
max_drawdown_pct
max_spread_bps
max_estimated_slippage_bps
min_expected_edge_after_cost_score
min_liquidity_threshold
stale data / stale portfolio / market session / kill switch gates
```

`max_daily_turnover_rub` is retained as a monitoring/audit signal for live
autonomous sandbox mode, not as a hard "stop trading today" cap. The gross
turnover mandate remains a mandatory constraint, but positive post-cost edge,
liquidity, exposure, loss/drawdown, market-session and current-cycle risk gates
decide whether a new order can proceed.

## 25. Runtime requirement

Docker/server runtime must use PostgreSQL-backed stores. In-memory repositories are allowed only for unit tests and isolated smoke runs. If `DATABASE_URL` is set and `APP_ENV` is not a test environment, `agent_app.main` uses PostgreSQL by default; `paper_trading` and `live_trading` must not silently run on in-memory state.

`scheduler_worker` is a persistent autonomous daemon. It must not be treated as a one-shot script in server deployment.

## 26. Live readiness

Before `live_trading`, the operator must check:

```sql
SELECT * FROM audit.live_trading_readiness_check ORDER BY check_name;
```

All rows must be `status = 'pass'`. This view checks active live risk policy, active live weights, turnover mandate, ArenaGo/MOEX provider config, tradable universe, live limits and live schedules.


---

# Дополнение v4 — Stabilized autonomous runtime chain

Версия дополнения: `4.0-autonomous-runtime-stabilization`
Статус: обязательная часть документации. Это дополнение фиксирует runtime-правила после стабилизации P0-блокеров автономного live-контура.

## 27. PostgreSQL migrations in Docker/server runtime

Docker/server runtime must not rely only on `/docker-entrypoint-initdb.d`, because that mechanism runs only for a fresh PostgreSQL volume. The compose runtime includes `migration_runner`, which executes:

```bash
python -m agent_app.storage.postgres.apply_migrations
```

The migration runner creates `audit.schema_migration`, applies sorted files from `agent_app/storage/postgres/migrations`, checks checksums for already applied migrations and fails fast if an applied migration was edited. Application services must start only after PostgreSQL is healthy and migrations completed successfully.

## 28. Autonomous scheduler behavior

`scheduler_worker` is a persistent daemon. It repeatedly triggers the orchestration entrypoint across the autonomous source set:

```text
Order Store
Raw Market Data Store
Raw Macro Data Store
Raw Text Store
Feature Store
Request Log Store
Audit Log Store
```

The worker remains outside analytical modules and does not bypass module boundaries. Its job is to keep the data/decision/risk/execution/portfolio/monitoring contours active on a server. Source-level locks/Redis queues may be added later, but the worker must never become a direct execution shortcut.

Current implementation uses in-process due checks per worker instance and does not yet implement Redis locks/queues. Running more than one scheduler replica is therefore a production blocker until distributed locking or a single-leader deployment policy is added. A single `scheduler_worker` instance is acceptable for controlled paper/staging runtime.

## 29. Runtime reference chain

Orchestration must carry `output_refs` from each executed module into subsequent `module_job.input_refs` in the same cycle. The intended current-cycle chain is:

```text
raw data / quality records
  -> feature_records / feature_vectors
  -> decision_set
  -> risk_check_result / approved order_intents
  -> execution_result / fill_report
  -> portfolio_snapshot
  -> monitoring/audit records
```

Refs ending in `:latest` are allowed only as repository read conveniences or non-execution fallbacks. Execution must receive explicit current-cycle `order_intent_refs`; if the current Risk Control run produced no approved order intents, Execution Engine must return `skipped` rather than execute stale latest orders.

## 30. Decision reason codes

`reason_codes` are not all blocking reasons. The Decision Engine separates hard-blocking reasons from explanatory/warning reasons. Codes such as `turnover_mandate_urgency` explain why an otherwise valid trade was prioritized and must not by themselves turn a decision into `block`.

## 31. Turnover math and pace tracking

`Portfolio State Module` calculates turnover mandate progress from realized fills/trades inside the rolling mandate window. The module must:

- sum actual 1-day turnover instead of taking `max` values;
- filter broker/ArenaGo trades to the mandate window;
- compute remaining turnover against remaining days, not the full window every time;
- project 14-day turnover from the observed average daily pace;
- classify `turnover_target_status` against expected progress for the elapsed part of the window.

This keeps the 10,000,000 RUB / 14-day target active without rewarding blind churn.

## 32. No-network in-memory execution

In-memory runtime is for tests and local smoke checks only. It must not inject the real External Request Gateway and must not perform live HTTP calls accidentally. PostgreSQL-backed paper/live runtime uses the gateway; in-memory runtime uses deterministic local repositories.

---

# Дополнение v5 — Predfinal runtime hardening and product-readiness gates

Версия дополнения: `5.0-predfinal-runtime-hardening`
Статус: обязательная часть документации. Это дополнение фиксирует предфинальные правила после аудита заглушек, источников, weights DB, scheduler/runtime, turnover mandate и соответствия кода документации.

## 33. Schedule-aware autonomous worker

`scheduler_worker` больше не должен быть простым бесконечным source-loop. В PostgreSQL-backed runtime он читает включённые записи `audit.schedule_config`, извлекает `interval_seconds` или `frequency`, пропускает чисто event-driven schedules без таймера и превращает due schedules в orchestration triggers.

Event-driven schedules вроде `on_decision_set` и `on_approved_order` не запускаются слепо по таймеру: они должны срабатывать от current-cycle refs, созданных Decision/Risk modules. Если PostgreSQL schedule config недоступен, worker может использовать fallback source-loop только для smoke/local recovery.

## 34. Docker service roles

В server runtime главным long-running процессом является `scheduler_worker`. `agent_app` является optional one-shot/manual service и не должен запускаться с `restart: unless-stopped`, иначе one-shot orchestration entrypoint превращается в скрытый restart-loop. `research_worker` также является optional batch service и включается отдельным Docker profile.

## 35. Disclosure source skip reasons

`Data Intake & Routing Module` может помечать planned discovery item как `source_missing_endpoint`, когда источник требует `issuer_ir_url` или другой per-issuer endpoint, но metadata выбранного инструмента ещё не содержит endpoint. Этот skip reason является валидным состоянием, а не ошибкой схемы БД. Такие записи должны попадать в monitoring как metadata coverage issue.

## 36. Daily loss units

Risk policy должна различать:

```text
max_daily_loss_rub
max_daily_loss_pct
```

`Risk Control Module` сначала ищет явный RUB-лимит. Если задан только `max_daily_loss_pct`, он конвертирует его в RUB через текущий `portfolio_snapshot.equity` / `cash` / `initial_capital_rub`. Нельзя трактовать `0.02` как рублёвый лимит убытка.

## 37. Live weights quality

Live autonomous profiles остаются отдельными от product baseline. После добавления turnover/churn terms live weights должны быть нормализованы: сумма весов каждого active live profile должна быть около `1.0`. `audit.metric_weights_readiness_check` проверяет не только наличие live profiles/rules, но и нормализацию product/live весов.

Bootstrap live weights всё ещё не являются доказанной прибыльной стратегией. Они являются governance seed для autonomous live contour. Перед реальным live-money запуском Codex/operator должен провести validation/backtest с реальными API/историей и подтвердить `validation_report_ref`.

## 38. Post-cost edge gate for profitable turnover

Turnover mandate не должен превращаться в churn. `Decision Engine Module` добавляет в decision payload:

```text
expected_edge_after_cost_score
execution_cost_estimate_bps
```

`Risk Control Module` использует `expected_edge_after_cost_score` как основной gate для turnover-driven decisions. Если explicit value отсутствует, Risk Control оценивает post-cost edge из `expected_edge_score` минус cost proxy по `spread_bps`, `estimated_slippage_bps`, `estimated_order_slippage_bps`, `commission_bps`.

Минимальное правило live режима:

```text
expected_edge_after_cost_score > min_expected_edge_after_cost_score
```

особенно для решений с `turnover_mandate_urgency`.

### Strategy quality hardening v2

Decision now carries both gross/pre-cost edge and signed post-cost economics:

```text
gross_expected_edge_score
expected_edge_after_cost_score
execution_cost_estimate_bps
commission_bps
edge_to_cost_ratio
position_effect
```

Action selection is net-edge-first: new long candidates require positive post-cost edge, and new short candidates require negative post-cost edge. Turnover urgency can prioritize candidates but cannot turn below-threshold economics into a trade.

`position_effect` removes ambiguous `sell` semantics:

```text
open_long / increase_long
reduce_long / close_long
open_short / increase_short
reduce_short / close_short
```

Existing profitable long positions may be partially reduced after `DECISION_TAKE_PROFIT_PCT` when post-cost continuation edge weakens. If continuation edge remains strong, the agent may keep part of the position. Stop-loss and non-positive post-cost edge can still trigger reduce/close.

Short selling is explicit capability, not an accidental `sell`. New/increased shorts require `DECISION_ALLOW_SHORT_SELLING=true`, `ARENA_GO_SHORTS_ALLOWED=true`, negative post-cost edge below `DECISION_SHORT_ENTRY_THRESHOLD` (default `0.012` in the ArenaGo sandbox runtime), and Risk Control approval. If shorts are disabled or provider capability is not confirmed, `open_short` / `increase_short` is rejected before execution with `short_selling_not_supported`; buy-to-cover is risk-reducing, not a new long.

Decision alpha/context features are midpoint-centered after normalization. A normalized value below `0.5` can reduce expected edge and, when strong enough after spread/slippage/commission costs, produce an explicit `open_short` candidate. Turnover urgency does not override this post-cost edge gate.

Risk Control assesses risk-reducing exits first, then ranks new long and new short candidates together by absolute post-cost edge strength. This keeps cycle limits from creating a long-only bias when a stronger short candidate is present.

## 39. Predfinal integration requirement

Перед финальным запуском на сервере нужно провести полноценный integration run с реальными ключами/сервисами или максимально близким staging:

```text
fresh PostgreSQL volume
  -> apply migrations 001..018
  -> database_readiness_check
  -> metric_weights_readiness_check
  -> live_trading_readiness_check
  -> MOEX ISS request
  -> CBR request
  -> public news/disclosure fetch
  -> PolzaAI GET /models healthcheck; strict JSON completion fallback
  -> ArenaGo get_bots/get_positions/get_trades
  -> mock or minimal safe submit_order path
  -> scheduler tick
  -> data -> features -> decision -> risk -> execution -> portfolio -> monitoring
```

Production-readiness считается недоказанной, пока этот сценарий не пройден в Docker с PostgreSQL-backed stores.

---

# Addendum v6 - Product-ready Docker/server runtime

Version: `6.0-product-ready-server-runtime`

## Docker commands

Use `docker/docker-compose.prod.yml` for server deployment. The production compose file does not publish PostgreSQL or Redis ports by default; services communicate on the internal compose network. Publish ports only through a server-specific override when an operator needs direct DB access.

```bash
cp config/api_keys.example.env config/api_keys.local.env
docker compose -f docker/docker-compose.prod.yml up --build -d postgres_local
docker compose -f docker/docker-compose.prod.yml run --rm migration_runner
docker compose -f docker/docker-compose.prod.yml up -d scheduler_worker
```

Controlled staging run:

```bash
docker compose -f docker/docker-compose.prod.yml --profile staging run --rm staging_runner
```

Shutdown/restart:

```bash
docker compose -f docker/docker-compose.prod.yml down
docker compose -f docker/docker-compose.prod.yml up -d scheduler_worker
```

## Runtime roles

`scheduler_worker` is the primary long-running process. `agent_app`, `research_worker` and `staging_runner` are optional/manual profiles and must not be used as restart-loop daemons.

`migration_runner` applies migrations `001..018` and repeated runs must skip already applied migrations. Readiness is checked through:

```sql
SELECT * FROM audit.database_readiness_check ORDER BY check_name;
SELECT * FROM audit.metric_weights_readiness_check ORDER BY check_name;
SELECT * FROM audit.live_trading_readiness_check ORDER BY check_name;
SELECT * FROM audit.allowed_universe_readiness_check ORDER BY check_name;
```

## Scheduler locking

The scheduler uses PostgreSQL-backed tick locks in `audit.scheduler_tick_lock` and still requires one active `scheduler_worker` replica unless a separate Redis/queue leader election layer is added. If Redis is unavailable, `SINGLE_SCHEDULER_INSTANCE=true` is required. The scheduler writes an audit warning for single-leader/no-Redis mode and refuses to start when neither Redis nor explicit single-leader mode is configured.

For controlled server smoke tests, the scheduler can be bounded without changing production behavior:

```env
SCHEDULER_ONCE=true
SCHEDULER_SCHEDULE_IDS=schedule:live_autonomous:portfolio_sync:1m
SCHEDULER_MAX_ENTRIES_PER_TICK=1
```

Each schedule emits JSON stdout events `scheduler_entry_started` and `scheduler_entry_finished` with duration and exit code.

## ArenaGo

ArenaGo token priority is `SANDBOX_API_KEY` first. `ARENA_GO_TOKEN` is only a local/dev fallback: production server startup treats missing `SANDBOX_API_KEY` as fatal unless `ALLOW_ARENA_GO_TOKEN_FALLBACK=true` is explicitly set. Logs and audit may show only a masked token source. Portfolio identity is resolved from `/api/bots` using exact `bots[].name`. `ARENA_GO_PORTFOLIO` and `ARENA_GO_BOT_NAME` may be empty; startup resolves and exports both from `/api/bots` when exactly one bot exists or when env matches a bot. `get_positions` and `get_trades` use the same exact, URL-encoded bot/portfolio name. Empty positions/trades are valid when the bot exists and `cash_balance` is available.

Internal `order_intent.quantity` is shares, but the current sandbox submit contour uses `ARENA_GO_SUBMIT_QUANTITY_UNITS=lots` so the provider payload is lot-converted. Sandbox live `submit_order` is allowed only when `SAFE_LIVE_SUBMIT=true`, `ARENA_GO_SANDBOX=true`, startup set `LIVE_READINESS_PASSED=true`, the portfolio and market data are fresh, the market is open, the instrument is valid, the order intent is from the current cycle, Risk approved it, and kill switches are off. `ERROR: MARKET CLOSED` is normalized as `market_closed`; the agent keeps syncing/monitoring and waits instead of crashing.

Allowed ArenaGo sandbox universe: `LKOH`, `SBER`, `ROSN`, `GAZP`, `VTBR`, `YDEX`, `PLZL`, `T`, `NVTK`, `X5`, `GMKN`, `MGNT`, `ALRS`, `AFLT`, `CHMF`, `NLMK`, `MOEX`, `SNGSP`, `MTSS`, `PIKK`. The agent must not trade outside this list.

## Single-container server deployment

Root `Dockerfile` is the autonomous server entrypoint. It starts local PostgreSQL inside the container, stores state in `/data`, applies migrations idempotently, resolves ArenaGo bot identity, syncs positions/trades, runs readiness checks, then starts `scheduler_worker` as the long-running loop.

Local/dev:

```bash
docker build -t holymoex:server .
docker run -d --name holymoex \
  --env-file config/api_keys.local.env \
  -v holymoex_data:/data \
  holymoex:server
```

Server:

```bash
docker build -t holymoex:server .
docker run -d --name holymoex \
  -e SANDBOX_API_KEY="$SANDBOX_API_KEY" \
  -e POLZA_API_KEY="$POLZA_API_KEY" \
  -e SYSTEM_MODE=automatic_live_trading \
  -e RUN_MODE=live_trading \
  -e ARENA_GO_SANDBOX=true \
  -e MARKET_SESSION_SOURCE=auto \
  -e ARENA_GO_MARKET_EXTENDED_SESSION=true \
  -e ARENA_GO_MARKET_CLOSE_TIME=23:50 \
  -e SINGLE_SCHEDULER_INSTANCE=true \
  -e SAFE_LIVE_SUBMIT=true \
  -v holymoex_data:/data \
  holymoex:server
```

Monitoring and lifecycle:

```bash
docker logs -f holymoex
docker stop holymoex
docker start holymoex
```

Controlled full-pipeline proof without real submit:

```bash
docker exec \
  -e CONTROLLED_PIPELINE_MARKET_OPEN_OVERRIDE=true \
  -e SAFE_LIVE_SUBMIT=false \
  -e EXECUTION_PROVIDER=mock \
  holymoex /app/scripts/controlled_full_pipeline.sh
```

This writes current-cycle `feature_record`, `feature_vector`, `decision_set`, `decision_record`, `risk_check_result`, `order_intent`, mock `execution_result/fill_report`, `portfolio_snapshot`, monitoring and `audit.module_job_result` records for 1-3 allowed instruments. It remains `RUN_MODE=live_trading` and never calls ArenaGo `submit_order`.

Pre-deploy check:

```bash
bash scripts/deploy_check.sh
```

`SAFE_LIVE_SUBMIT` defaults to `false` for dry-run/staging safety. Set it to `true` only for the ArenaGo sandbox/test contour after `SANDBOX_API_KEY`, readiness, portfolio sync and risk gates are verified.

Startup sequence:

```text
load env
  -> ensure /data directories
  -> start persistent local PostgreSQL
  -> apply migrations 001..018
  -> validate ArenaGo token through SANDBOX_API_KEY/ARENA_GO_TOKEN
  -> resolve exact bot/portfolio from /api/bots
  -> sync positions/trades
  -> run readiness views
  -> export LIVE_READINESS_PASSED
  -> start autonomous scheduler
```

Restart keeps `/data`, so migrations skip already applied SQL, turnover progress is preserved, stale orders are not replayed, and the first live step is always ArenaGo portfolio/trades sync before execution.

In standalone root-container mode the launcher uses its own `/data/postgres` database even if a compose-style `DATABASE_URL=postgres_local` is present in the env file. Set `HOLYMOEX_USE_EXTERNAL_DATABASE=true` only when the single container should deliberately connect to an external PostgreSQL instance.

## PolzaAI

Gateway supports `polza_ai/models` through `GET ${POLZA_BASE_URL}/models`. If an environment/provider later disables that endpoint, the supported fallback healthcheck is a strict JSON `llm_completion` smoke with `response_format={"type":"json_object"}` and schema fields `schema_version`, `model_id`, `model_version`, `task_type`, `items`.

Task-specific model routing has priority. `POLZA_LLM_MODEL` is kept only as an empty compatibility placeholder and is ignored by server runtime if it points to an expensive legacy model:

```env
POLZA_FAST_MODEL=deepseek/deepseek-v4-flash
POLZA_REASONING_MODEL=qwen/qwen3.6-35b-a3b
POLZA_DEFAULT_MODEL=qwen/qwen3.6-35b-a3b
LLM_ENABLED=true
LLM_MAX_CALLS_PER_MINUTE=2
LLM_MAX_CALLS_PER_HOUR=30
LLM_MAX_CALLS_PER_DAY=200
LLM_MAX_ITEMS_PER_RUN=3
LLM_MIN_SECONDS_BETWEEN_CALLS=2
ALLOW_LLM_FALLBACK=false
ENABLE_LLM_TEXT_SCHEDULES=true
RAW_TEXT_FALLBACK_INTERVAL_SECONDS=1800
MARKET_DATA_FETCH_RAW_TRADES=false
LIQUIDITY_FETCH_RAW_TRADES=false
PIPELINE_LOOKBACK_MINUTES=240
ARENA_GO_EXTENDED_LOOKBACK_MINUTES=480
```

`deepseek/deepseek-v4-flash` is used for light text tasks: `event_extraction`, `sentiment_scoring`, `news_classification`, entity/ticker matching, duplicate/novelty pre-classification, simple disclosure classification, short news summarization and raw-text relevance filtering. `qwen/qwen3.6-35b-a3b` is used for heavier reasoning tasks: `report_extraction`, `earnings_analysis`, long-report dividend extraction, `macro_text_analysis`, complex corporate actions, multi-source synthesis, validation/research commentary and strategy/risk explanations. LLM output remains strict JSON only and must not contain buy/sell recommendations, weight changes, risk-policy changes, order intents or free-form prose.

The LLM cache key includes `content_hash`, `task_type`, `prompt_version`, `model_id` and `event_ontology_version`. It intentionally excludes volatile fields such as `job_id`, `fetched_at`, current timestamp and scheduler tick id.

Validated EventNews envelopes with `"items": []` are treated as a successful `no_event_found` result for irrelevant text. They are audited and are not retried as schema failures.

In the autonomous live loop, MOEX candle/index data and ArenaGo portfolio sync are the primary realtime inputs. The default realtime lookback is `PIPELINE_LOOKBACK_MINUTES=240`; during the ArenaGo sandbox evening session it is automatically raised to at least `ARENA_GO_EXTENDED_LOOKBACK_MINUTES=480` so the agent can still use the latest MOEX cash-session candles without treating them as fake-fresh. MOEX raw trade tape fetch is opt-in via `MARKET_DATA_FETCH_RAW_TRADES=true` and `LIQUIDITY_FETCH_RAW_TRADES=true`; by default it is disabled so the scheduler can reach feature vector, decision, risk and execution instead of blocking on heavy `/trades` backfill. Scheduled text jobs are enabled with `ENABLE_LLM_TEXT_SCHEDULES=true`: the fast news contour runs a cheap flash extraction pass about every 2 minutes over fresh/unprocessed `raw_text`, while expensive qwen reasoning is used only when the fast pass finds material, high-impact, or strongly negative/positive news.

Russian routing markers are supported for reports/dividends/disclosures, including `отчет`, `отчёт`, `дивиденды`, `совет директоров`, `МСФО`, `РСБУ`, `финансовые результаты`, `операционные результаты`, `собрание акционеров` and `существенный факт`.

To inspect PolzaAI usage/cost:

```sql
SELECT date_trunc('minute', received_at) AS minute,
       count(*) AS calls,
       sum(cost_units) AS cost_units
  FROM request_logs.external_response
 WHERE provider = 'polza_ai'
 GROUP BY 1
 ORDER BY 1 DESC
 LIMIT 60;
```

Steady-state Raw Text discovery and EventNews extraction are split by cost. `schedule:data_intake:scheduled_external_news_discovery` and `schedule:event_news:intake` run a capped fast-news pass every 2 minutes after migration `032_fast_news_llm_reactivity.sql`; EventNews uses `deepseek/deepseek-v4-flash` for ordinary classification/extraction and escalates only material/high-impact items to `qwen/qwen3.6-35b-a3b`. Earnings/fundamental long-report paths remain slower and reasoning-oriented. The LLM still writes structured events/features only; it does not generate orders, weights, or risk policy.

## Market-hours gating

The runtime exposes `market_session_status = open | closed | premarket | postmarket | unknown` and derives `agent_runtime_phase = trading_session | off_market | degraded`. With `MARKET_SESSION_SOURCE=auto` and `ARENA_GO_SANDBOX=true`, the server uses a provider-driven ArenaGo sandbox session probe by default, not only a fixed wall-clock window. Every `ARENA_GO_SESSION_PROBE_INTERVAL_SECONDS` seconds the scheduler sends safe `get_bots` and `get_positions` requests through the External Request Gateway, writes `audit.audit_record.event_type='arena_go_session_probe'`, and `current_market_session()` uses that fresh probe across Decision, Risk, Execution and Monitoring. `ERROR: MARKET CLOSED` from ArenaGo submit remains authoritative: it closes the internal session for `ARENA_GO_MARKET_CLOSED_COOLDOWN_SECONDS` and prevents retry loops.

The clock-based ArenaGo window is now a fallback/profile, not the primary server gate. The default fallback window is `ARENA_GO_MARKET_OPEN_TIME=10:00` to `ARENA_GO_MARKET_CLOSE_TIME=23:50` Europe/Moscow; set `MARKET_SESSION_SOURCE=moex` to force strict MOEX cash-session gating.

Outside `open`, the scheduler skips heavy live `Decision Engine`, `Risk Control` and `Execution Engine` loops and throttles LLM-heavy Raw Text/EventNews jobs. Portfolio sync, health/readiness, monitoring/audit and light market/macro maintenance may continue. If session status is `unknown`, live submit is blocked and monitoring/audit should surface a warning.

```env
MARKET_SESSION_SOURCE=auto
ARENA_GO_SESSION_PROBE_ENABLED=true
ARENA_GO_SESSION_PROBE_REQUIRED_FOR_OPEN=true
ARENA_GO_SESSION_PROBE_INTERVAL_SECONDS=120
ARENA_GO_SESSION_PROBE_MAX_AGE_SECONDS=300
ARENA_GO_SESSION_PROBE_REQUIRE_POSITIONS=true
ARENA_GO_MARKET_CLOSED_COOLDOWN_SECONDS=180
ARENA_GO_MARKET_TIMEZONE=Europe/Moscow
ARENA_GO_MARKET_EXTENDED_SESSION=true
ARENA_GO_MARKET_OPEN_TIME=10:00
ARENA_GO_MARKET_CLOSE_TIME=23:50
ALLOW_ARENA_GO_EXTENDED_MARKET_DATA_GRACE=true
ARENA_GO_EXTENDED_MARKET_DATA_GRACE_SECONDS=21600
```

During the ArenaGo sandbox extended window, MOEX cash-session candles may stop updating before ArenaGo stops accepting sandbox orders. `ALLOW_ARENA_GO_EXTENDED_MARKET_DATA_GRACE=true` lets recent MOEX-derived intraday price/liquidity features remain usable until `ARENA_GO_EXTENDED_MARKET_DATA_GRACE_SECONDS` expires. The feature vector is still flagged with `arena_go_extended_session_market_data_grace`; future timestamps and genuinely missing data remain blocked.

Production fallback Raw Text/EventNews source-loop is disabled by default when `audit.schedule_config` cannot be loaded. To enable it deliberately:

```env
ALLOW_LLM_FALLBACK=true
RAW_TEXT_FALLBACK_INTERVAL_SECONDS=1800
```

The fallback interval must be at least 900 seconds. The scheduler writes `raw_text_fallback_disabled_in_production` when it refuses the production fallback.

## Disclosure and issuer IR

`e-disclosure.ru` may be blocked by anti-bot controls. This is treated as blocked/unhealthy source status, not as module failure. Official disclosure fallback sources are `disclosure.1prime`, `disclosure.ru/AK&M`, issuer corporate/IR sites when `instrument_profile.metadata.issuer_ir_url` is populated, and public news confirmation. Missing issuer IR URLs are reported through `audit.registry_reconciliation_report` with `missing_issuer_ir_url`; corporate-site discovery may controlled-skip with `source_missing_endpoint`.

## Controlled staging pipeline

`agent_app.staging_runner` performs a bounded end-to-end staging cycle:

```text
limited instruments/news
  -> raw_text / source refs
  -> feature_record / feature_vector
  -> decision_set
  -> risk_check_result
  -> current-cycle order_intent
  -> mock execution_result / fill_report
  -> portfolio_snapshot turnover metrics
  -> monitoring_record / audit_record
```

The runner caps instruments to 1-3, caps news items, never submits live orders, and records refs in Audit/Monitoring stores.

## Live sandbox policy

The primary server runtime is autonomous automatic live trading in the ArenaGo sandbox/test contour: `SYSTEM_MODE=automatic_live_trading`, `RUN_MODE=live_trading`, `ARENA_GO_SANDBOX=true`. The turnover target above `10_000_000 RUB` is a mandatory constraint, not the alpha objective. The primary objective remains portfolio value and positive post-cost expected return. Turnover urgency can increase activity, but it cannot bypass stale data checks, portfolio sync, daily loss/drawdown limits, liquidity/spread/slippage limits, current-cycle order refs, or the post-cost edge gate.
