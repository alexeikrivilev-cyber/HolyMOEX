# Module Database Access Matrix

Этот файл фиксирует рабочую матрицу доступов модулей к логическим БД/stores проекта.

Правило по умолчанию: модуль получает только `read` на входные stores и только `write` на stores, которые прямо указаны в его документации. `DELETE`, DDL и изменение governance-данных не выдаются runtime-модулям.

## Доступы по модулям

| Модуль | Read | Write |
|---|---|---|
| `01 Orchestration Module` | `Schedule Config Store`, `Module Dependency Graph Store` | `Audit Log Store`, `Module Job Store`, `Module Run Store`, `Module Job Result Store` |
| `02 External Request Gateway Module` | `Provider Config Store`, `Request Cache Store` | `Request Cache Store`, `Request Log Store`, `Raw Market Data Store`, `Raw Text Store`, `Raw Macro Data Store` |
| `03 Selected Instruments Registry Module` | `Selected Instruments DB`, `Raw Market Data Store` | `Selected Instruments DB`, `Audit Log Store` |
| `04 Data Intake & Routing Module` | `Selected Instruments DB` | `Raw Text Store`, `Event Routing Store`, `Audit Log Store` |
| `05 Data Quality Module` | `Raw Market Data Store`, `Raw Text Store`, `Feature Store`, `Portfolio State Store` | `Data Quality Store`, `Audit Log Store` |
| `06 Market Data Metrics Module` | `Selected Instruments DB`, `Raw Market Data Store` | `Feature Store`, `Audit Log Store` |
| `07 Liquidity & Microstructure Module` | `Raw Market Data Store`, `Selected Instruments DB`, `Metric Weights DB` | `Feature Store`, `Execution Constraint Store` |
| `08 Volatility & Risk Metrics Module` | `Raw Market Data Store`, `Raw Macro Data Store` | `Feature Store`, `Risk Context Store` |
| `09 Market Context Module` | `Raw Macro Data Store`, `Raw Market Data Store`, `Event Store` | `Feature Store`, `Market State Store` |
| `10 Fundamental & Valuation Module` | `Raw Text Store`, `Event Store`, `Raw Market Data Store` | `Feature Store`, `Fundamental Snapshot Store` |
| `11 Event & News Intelligence Module` | `Raw Text Store`, `Event Routing Store`, `Selected Instruments DB` | `Event Store`, `Feature Store`, `Audit Log Store` |
| `12 Earnings & Dividend Intelligence Module` | `Raw Text Store`, `Event Store`, `Raw Market Data Store` | `Event Store`, `Feature Store`, `Earnings Dividend Store` |
| `13 Corporate Actions Adjustment Module` | `Event Store`, `Selected Instruments DB`, `Raw Market Data Store` | `Selected Instruments DB`, `Raw Market Data Store`, `Corporate Actions Store`, `Feature Store` |
| `14 Derivatives & Positioning Module` | `Raw Market Data Store`, `Selected Instruments DB` | `Feature Store`, `Derivatives Availability Store` |
| `15 Normalization & Feature Vector Module` | `Feature Store`, `Data Quality Store`, `Selected Instruments DB` | `Feature Store` |
| `16 Feature Validation & Research Module` | `Feature Store`, `Order Store`, `Market State Store` | `Research Store`, `Metric Weights DB` только draft |
| `17 Decision Engine Module` | `Feature Store`, `Metric Weights DB`, `Portfolio State Store`, `Market State Store` | `Decision Store`, `Audit Log Store` |
| `18 Risk Control Module` | `Decision Store`, `Risk Policy Store`, `Portfolio State Store`, `Feature Store` | `Risk Store`, `Order Store`, `Audit Log Store` |
| `19 Execution Engine Module` | `Order Store`, `Selected Instruments DB`, `Risk Store`, `Portfolio State Store` | `Order Store`, `Execution Log Store`, `Audit Log Store` |
| `20 Portfolio State Module` | `Order Store`, `Raw Market Data Store` | `Portfolio State Store`, `Audit Log Store` |
| `21 Backtesting & Paper Trading Module` | `Raw Market Data Store`, `Feature Store`, `Metric Weights DB`, `Risk Policy Store`, `Corporate Actions Store` | `Research Store`, `Order Store` |
| `22 Monitoring & Audit Module` | `Audit Log Store`, `Module Job Result Store`, `Request Log Store`, `Decision Store`, `Order Store`, `Portfolio State Store` | `Monitoring Store`, `Audit Log Store` |

## Store to PostgreSQL mapping

| Store | PostgreSQL schema / tables |
|---|---|
| `Selected Instruments DB` | `registry.instrument_universe`, `registry.instrument_profile`, `registry.instrument_alias`, `registry.instrument_mapping`, `registry.universe_snapshot` |
| `Raw Market Data Store` | `raw_market.raw_candle`, `raw_market.raw_trade`, `raw_market.raw_orderbook`, `raw_market.raw_index_value` |
| `Raw Text Store` | `raw_text.raw_text_item`, `raw_text.text_source_config`, discovery/search tables |
| `Raw Macro Data Store` | `raw_macro.raw_macro_point` |
| `Event Store` | `events.structured_event`, `events.event_cluster`, `events.event_reaction` |
| `Feature Store` | `features.feature_record`, `features.feature_vector` |
| `Metric Weights DB` | `weights.weights_profile`, `weights.metric_weight_rule` |
| `Risk Policy Store` | `risk.risk_policy`, `risk.instrument_limit`, `risk.portfolio_limit` |
| `Risk Store` | `risk.risk_check_result`, `risk.risk_context_record` |
| `Portfolio State Store` | `portfolio.portfolio_snapshot`, `portfolio.position_state` |
| `Decision Store` | `decisions.decision_record`, `decisions.decision_explanation` |
| `Order Store` | `orders.order_intent`, `orders.order_status`, `orders.fill_report`, `orders.execution_result` |
| `Request Log Store` | `request_logs.external_request`, `request_logs.external_response`, `request_logs.external_request_log` |
| `Provider Config Store` | `request_logs.provider_config` |
| `Request Cache Store` | `request_logs.request_cache` |
| `Audit Log Store` | `audit.audit_record` |
| `Schedule Config Store` | `audit.schedule_config` |
| `Module Dependency Graph Store` | `audit.module_dependency_graph` |
| `Module Job Store` | `audit.module_job` |
| `Module Run Store` | `audit.module_run` |
| `Module Job Result Store` | `audit.module_job_result` |
| `Research Store` | `audit.research_report` |
| `Monitoring Store` | `audit.monitoring_record` |
| `Market State Store` | `features.market_state_record` |
| `Execution Constraint Store` | `raw_market.execution_constraint` |
| `Fundamental Snapshot Store` | `features.fundamental_snapshot` |
| `Earnings Dividend Store` | `events.earnings_dividend_record` |
| `Corporate Actions Store` | `events.corporate_action_record` |
| `Derivatives Availability Store` | `raw_market.derivatives_availability` |
| `Data Quality Store` | `features.data_quality_record` |
| `Event Routing Store` | `raw_text.event_routing_message` |

## БД, которые нельзя редактировать без явного требования владельца

Эти stores являются governance/manual-зоной. Их нельзя менять автоматически или “по ходу задачи”, если владелец проекта явно не попросил это сделать.

| Store | Почему важно | Кто может менять |
|---|---|---|
| `Selected Instruments DB` | Определяет торговую вселенную, идентификаторы, FIGI/ISIN, ArenaGo mapping, `tradable`, `execution_enabled` | Только вручную или через approved registry job |
| `Metric Weights DB` | Определяет веса decision/composite метрик; ошибка меняет поведение стратегии | Владелец/governance; Research может писать только draft |
| `Risk Policy Store` | Определяет лимиты риска, exposure, loss limits, kill switches, live/paper policy | Только владелец/governance |
| `Provider Config Store` | Определяет разрешённые внешние провайдеры и env bindings | Только владелец/governance |
| `Schedule Config Store` | Определяет, какие модули и как часто запускаются | Только владелец/governance |
| `Module Dependency Graph Store` | Определяет пересчёт модулей после изменений данных | Только владелец/governance |
| `Portfolio State Store` initial records | Начальный капитал, portfolio id, стартовый cash/positions | Владелец; runtime обновляет только текущие snapshots/state |
| `Text Source Config` | Определяет источники новостей, раскрытий, corporate sites | Только владелец/governance |
| `Market State Store` manual overrides | Может влиять на decision/execution режимы рынка | Владелец/governance; runtime может писать вычисленные records |
| `Research Store` validation approvals | Основание для активации весов и policy | Research пишет отчёты; approvals только владелец/governance |

## Практические правила доступа

- Runtime-модуль не получает доступ ко всей БД целиком.
- `read` означает `SELECT` только на нужные таблицы.
- `write` означает `INSERT` и, если нужно для idempotency, ограниченный `UPDATE` только на output tables.
- `DELETE` не выдаётся по умолчанию.
- DDL права выдаются только миграционному/admin-пользователю.
- Запись в `weights`, `risk`, `registry`, `orders`, `portfolio` выдаётся особенно осторожно.
- Любое изменение governance/manual-зоны должно оставлять запись в `Audit Log Store`.
