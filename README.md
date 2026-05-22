# MOEX Hybrid AI Trading Agent — Technical Documentation

Версия: `1.0-strict`  
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

The previous `strict_default` profiles are retained for audit/replay but seeded as `deprecated`. Runtime schedules must reference `product_baseline` profiles. Live-trading weights remain inactive until a separate governance process approves a live-specific version.

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
| `scheduler_worker` | optional worker для scheduled jobs |
| `research_worker` | optional worker для research/backtest jobs |

Для строгой первой сборки допустимо держать `agent_app`, `scheduler_worker` и `research_worker` как один image с разными entrypoint. БД должна быть отдельным Docker service.

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

Запрещено хранить ключевые trading state только в памяти контейнера. После рестарта должны восстанавливаться: `portfolio_snapshot`, `position_state`, `decision_record`, `order_intent`, `execution_result`, `external_request`, `external_response`, `external_request_log`, `module_job`, `module_job_result`, `module_run`, `feature_record`.

### 16.1.1 Database readiness contract

Миграции PostgreSQL находятся в `agent_app/storage/postgres/migrations` и применяются строго по имени файла. В `docker/docker-compose.example.yml` этот каталог монтируется в `/docker-entrypoint-initdb.d`, поэтому новая локальная база инициализируется схемой и governance seed-данными автоматически при первом старте volume.

После применения миграций базовый контроль готовности выполняется запросом:

```sql
SELECT *
  FROM audit.database_readiness_check
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

### 16.2 Docker environment contract

Все конфигурации должны приходить из environment variables или mounted config files. Секреты не хранятся в markdown, коде или git.

Required env variables:

```env
APP_ENV=local
RUN_MODE=paper_trading
DATABASE_URL=postgresql://moex_agent:moex_agent_password@postgres_local:5432/moex_agent
REDIS_URL=redis://redis_local:6379/0
DEFAULT_TIMEZONE=Europe/Moscow
INITIAL_CAPITAL_RUB=1000000
SELECTED_UNIVERSE_ID=moex_top20_manual
ARENA_GO_BASE_URL=https://arenago.ru/api
ARENA_GO_TOKEN=replace_with_real_token
ARENA_GO_PORTFOLIO=MyBot
ARENA_GO_BOT_NAME=MyTradingBot
ARENA_GO_DAILY_TRADE_LIMIT=1000
POLZA_BASE_URL=https://polza.ai/api/v1
POLZA_API_KEY=replace_with_real_key
POLZA_LLM_MODEL=deepseek/deepseek-v4-pro
LLM_DEFAULT_TEMPERATURE=0
LLM_DEFAULT_RESPONSE_FORMAT=json_object
```

Файл с ключами должен быть отдельным локальным файлом: `config/api_keys.local.env`. В репозитории хранится только шаблон: `config/api_keys.example.env`.

## 17. ArenaGo execution integration

Торговая платформа: `ArenaGo`. Начальный капитал системы: `1000000 RUB`. Базовый режим запуска до ручного переключения: `paper_trading`. Для `live_trading` нужен отдельный `risk_policy` и активный `weights_profile` с `run_mode_allowed` включая `live_trading`.

### 17.1 ArenaGo provider

`External Request Gateway Module` должен поддерживать provider:

```json
{
  "provider": "arena_go",
  "base_url_env": "ARENA_GO_BASE_URL",
  "auth_header": "Authorization",
  "auth_value_source": "ARENA_GO_TOKEN",
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
Authorization: ${ARENA_GO_TOKEN}
```

```json
{
  "direction": "B",
  "secid": "SBER",
  "quantity": 10,
  "bot": "MyTradingBot"
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

Important: ArenaGo `quantity` is treated as shares/units unless platform configuration explicitly says lots. `Selected Instruments Registry Module` must store `arena_go_quantity_mode = shares | lots`. `Execution Engine Module` must convert `target_quantity` into ArenaGo `quantity` using this field.

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

LLM-провайдер: `PolzaAI`. Основная модель: `deepseek-v4-pro`. Точный `model_id` должен быть проверен через `GET /models` перед production-запуском и храниться в `POLZA_LLM_MODEL`. По умолчанию в конфигурации используется `deepseek/deepseek-v4-pro`.

PolzaAI вызывается только через `External Request Gateway Module`. LLM-модули не имеют права напрямую создавать HTTP-клиент к PolzaAI.

Gateway request для LLM:

```json
{
  "request_id": "string",
  "caller_module": "Event & News Intelligence Module",
  "provider": "polza_ai",
  "request_type": "llm_completion",
  "payload": {
    "model": "deepseek/deepseek-v4-pro",
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
13. Fresh PostgreSQL volume applies all files from `agent_app/storage/postgres/migrations` through the compose init mount.
14. `audit.database_readiness_check` returns only `status = 'pass'` rows before runtime assembly.
15. `audit.metric_weights_readiness_check` returns only `status = 'pass'` rows and Decision schedule references `weights:product_baseline:*:v1`.
16. Any future empirically optimized `Metric Weights DB` proposal starts from `prompts/metric_weights_optimization_prompt.md` and remains `draft` until manual governance approval.
